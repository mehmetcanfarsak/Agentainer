#!/usr/bin/env python3
"""Agentainer -- the file-based MAILROOM (the heart of v2).

This module replaces v1's XML-envelope-in-prose messaging. The agent's entire
world is two verbs (read a file, write a file) and four folders (inbox /
outbox / read / sent). The model only reads and writes natural-language files;
everything hard -- routing, ACL, message IDs, threading, read-state, queueing,
retries, availability, the durable log -- is deterministic orchestrator code in
this file. See ``ProjectPlan.md`` (§4-§14, §24).

Design, in one screen:

  * ``inbox/`` holds EXACTLY ONE message at a time (one-at-a-time release). The
    rest wait in ``cfg.queue_dir/<agent>/``.
  * ``outbox/<name>/about.md`` is the orchestrator-maintained contact card; its
    mere presence IS the ACL (only peers in ``can_talk_to`` get a folder).
  * Read-state is orchestrator-owned; moving a message to ``read/`` is a
    best-effort receipt; an auto-archive fallback guarantees liveness so a
    forgetful model can never wedge the swarm.
  * ``system`` = orchestrator voice (no folder; just enqueue + log). ``user`` =
    virtual human mailbox with an ACL gate + an availability toggle (default OFF)
    that HOLDS mail (never bounces) + sends a ``system`` ack.

Zero runtime dependencies: Python stdlib + the bundled lib/ modules only.

Branding: "swarm" is retired -- it's Agentainer everywhere (decision D21).
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

# The lib modules are imported bare (config, log, ...) because the test harness
# and the CLI both put ``lib/`` on ``sys.path``. Keep these imports in sync with
# the other lib modules.
import config as cfgmod  # noqa: E402
from config import Agent, SwarmConfig  # noqa: E402

import log  # noqa: E402
import lock  # noqa: E402
import turn  # noqa: E402
import tmux  # noqa: E402


# --------------------------------------------------------------------------
# module constants
# --------------------------------------------------------------------------

# A message presented this many times without being handled is auto-archived
# (plan §7) so a forgetful model can never wedge the swarm.
AUTO_ARCHIVE_PRESENTATIONS = 5

# Runaway-loop cap: at most this many messages between any pair of agents within
# the sliding window below, else we drop (rate-limit) further ones. Cheap
# insurance against A<->B "thanks!/you're welcome!" loops.
RUNAWAY_CAP = 20
RUNAWAY_WINDOW_S = 60

# Sentinel prefix marking a periodic-ping message, so maybe_ping can detect a
# still-unhandled ping and avoid piling them up.
PING_MARKER = "ping-"


# --------------------------------------------------------------------------
# run-dir small-JSON helpers (presentation counts, read receipts, pings)
# --------------------------------------------------------------------------


def _load_run_json(cfg: SwarmConfig, name: str) -> dict:
    """Load a small JSON state file from ``cfg.run_dir``, returning {} if absent."""
    p = cfg.run_dir / name
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_run_json(cfg: SwarmConfig, name: str, data: dict) -> None:
    """Persist a small JSON state file into ``cfg.run_dir``."""
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / name).write_text(json.dumps(data))


def _get_presentations(cfg: SwarmConfig, agent_name: str) -> dict:
    return _load_run_json(cfg, f"{agent_name}.presentations.json")


def _set_presentations(cfg: SwarmConfig, agent_name: str, msg_id: str, count: int) -> None:
    _save_run_json(cfg, f"{agent_name}.presentations.json", {"msg_id": msg_id, "count": count})


def _bump_presentations(cfg: SwarmConfig, agent_name: str, msg_id: str) -> None:
    """Record one more presentation of *msg_id* for *agent_name* (liveness)."""
    cur = _get_presentations(cfg, agent_name)
    if cur.get("msg_id") == msg_id:
        cur["count"] = cur.get("count", 0) + 1
    else:
        cur = {"msg_id": msg_id, "count": 1}
    _save_run_json(cfg, f"{agent_name}.presentations.json", cur)


# --------------------------------------------------------------------------
# HELPERS (private)
# --------------------------------------------------------------------------


def new_message_id() -> str:
    """Return a fresh message id, e.g. ``m-1a2b3c4d``."""
    return "m-" + uuid.uuid4().hex[:8]


def format_header(from_, to, msg_id, time, re_=None) -> str:
    """Build the From/To/Id/Time/(Re) header block. The model never writes this."""
    lines = [f"From: {from_}", f"To: {to}", f"Id: {msg_id}", f"Time: {time}"]
    if re_ is not None:
        lines.append(f"Re: {re_}")
    return "\n".join(lines)


def stamp_message(body, from_, to, msg_id, re_=None) -> str:
    """Header + blank line + body. The orchestrator stamps every message."""
    return format_header(from_, to, msg_id, log.now_iso(), re_) + "\n\n" + body


def mark_read(cfg: SwarmConfig, sender: str, msg_id: str) -> None:
    """Record a read receipt for *msg_id* (originally from *sender*).

    Best-effort: the authoritative read-state lives in the recipient's own
    ``read/`` folder + ``<agent>.read.json`` processed list, so a duplicate
    receipt here can never wedge anything. We log it so the event is durable.
    """
    data = _load_run_json(cfg, f"{sender}.readreceipts.json")
    data[msg_id] = log.now_iso()
    _save_run_json(cfg, f"{sender}.readreceipts.json", data)
    log.log_event(cfg, sender, "read-receipt", id=msg_id)


def enqueue(cfg: SwarmConfig, recipient: str, text: str, msg_id: str) -> None:
    """Write *text* into ``cfg.queue_dir/<recipient>/<msg_id>.txt`` (the queue).

    After the message is durably queued, best-effort mirror it to Telegram (a
    no-op unless configured). Mirroring runs AFTER the write and can never raise
    -- correctness never depends on the network (see lib/telegram.py).
    """
    with lock.file_lock(cfg, recipient, "mail"):
        q = cfg.queue_dir / recipient
        q.mkdir(parents=True, exist_ok=True)
        (q / f"{msg_id}.txt").write_text(text)
    import telegram  # lazy: keeps mail's import graph free of the bridge

    telegram.on_enqueued(cfg, recipient, text, msg_id)


def rate_limited(cfg: SwarmConfig, a: str, b: str) -> bool:
    """Per-pair sliding-window runaway-loop cap.

    Returns True if the pair (a, b) has already exchanged >= ``RUNAWAY_CAP``
    messages in the last ``RUNAWAY_WINDOW_S`` seconds; otherwise records this
    one and returns False.
    """
    key = "-".join(sorted([a, b]))
    path = cfg.run_dir / f"{key}.loop.json"
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        data = json.loads(path.read_text()) if path.exists() else []
    except (json.JSONDecodeError, OSError):
        data = []
    cutoff = now - RUNAWAY_WINDOW_S
    data = [t for t in data if t >= cutoff]
    data.append(now)
    path.write_text(json.dumps(data))
    return len(data) > RUNAWAY_CAP


def _take_outbox_file(cfg: SwarmConfig, sender: str, recipient: str, body: str):
    """Find the outbox message file for (sender -> recipient) whose content matches *body*.

    Never returns the ``about.md`` contact card.
    """
    mp = cfg.mail_paths(cfg.get(sender))
    d = mp.outbox / recipient
    if not d.exists():
        return None
    files = [f for f in sorted(d.iterdir()) if f.is_file() and f.name != "about.md"]
    for f in files:
        try:
            if f.read_text() == body:
                return f
        except OSError:  # pragma: no cover - defensive only
            continue
    return files[0] if files else None


def _move_outbox_file(cfg: SwarmConfig, sender: str, recipient: str, body: str, dest: Path) -> None:
    """Move the matching outbox file for (sender -> recipient) into *dest*."""
    f = _take_outbox_file(cfg, sender, recipient, body)
    if f is None:
        return
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(f), str(dest / f.name))


def _parse_header_field(text: str, field: str):
    m = re.search(rf"^{re.escape(field)}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------------
# PUBLIC API
# --------------------------------------------------------------------------


def init_mailboxes(cfg: SwarmConfig) -> None:
    """Create every agent's five mailbox folders + per-agent queue + outbox ACL folders."""
    with lock.file_lock(cfg, "init", "mail"):
        for agent in cfg.agents:
            mp = cfg.mail_paths(agent)
            for d in (mp.inbox, mp.outbox, mp.read, mp.sent, mp.failed):
                d.mkdir(parents=True, exist_ok=True)
            (cfg.queue_dir / agent.name).mkdir(parents=True, exist_ok=True)
            for peer in agent.can_talk_to:
                (mp.outbox / peer).mkdir(parents=True, exist_ok=True)
        write_contact_cards(cfg)


def write_contact_cards(cfg: SwarmConfig) -> None:
    """(Re)write every ``outbox/<peer>/about.md`` contact card.

    The mere presence of a peer's folder+card IS the ACL: only agents listed in
    ``can_talk_to`` get one. For ``user`` the card's Status reflects
    ``cfg.user_available`` (available / away).
    """
    for a in cfg.agents:
        for b in cfg.agents:
            if a.name in b.can_talk_to:
                mp = cfg.mail_paths(b)
                card = mp.outbox / a.name / "about.md"
                card.parent.mkdir(parents=True, exist_ok=True)
                card.write_text(
                    f"Name: {a.name}\nRole: {a.role}\nStatus: available\n"
                )
    status = "available" if cfg.user_available else "away"
    for b in cfg.agents:
        if "user" in b.can_talk_to:
            mp = cfg.mail_paths(b)
            card = mp.outbox / "user" / "about.md"
            card.parent.mkdir(parents=True, exist_ok=True)
            card.write_text(
                f"Name: user\nRole: human operator\nStatus: {status}\n"
            )


def standby_prompt(cfg: SwarmConfig, agent) -> str:
    """Build the FIRST message an agent receives at ``up`` (its initialization).

    The agent's standing ``role`` (its identity + how to use the mailbox) is
    delivered up front, wrapped with an explicit STANDBY notice: no task has been
    assigned yet, so the agent must NOT initiate any mail. This stops a proactive
    model from spinning up at startup and mailing its peers before any real task
    exists -- the human delivers the first task via ``agentainer send``, and the
    normal nudge is what notifies the agent when that task lands.

    Principle 3: the model is always told its exact mailbox paths, so the standby
    states them rather than assuming the agent knows them.
    """
    mp = cfg.mail_paths(agent)
    allowed = ", ".join(p for p in agent.can_talk_to if p != "system") or "(no one yet)"
    notice = (
        "\n---\n"
        "This is your initialization message. No task has been assigned to you yet.\n\n"
        "Do NOT write any file to your outbox/ and do NOT send any message. "
        "You will be notified (a new message will appear in your inbox) when your "
        "first real task arrives. Until then, simply wait and take no action.\n\n"
        "Your mailbox (for when a task arrives):\n"
        f"  inbox:   {mp.inbox}\n"
        f"  outbox:  {mp.outbox}   (write a file into outbox/<name>/ to send)\n"
        f"  read:    {mp.read}    (move a handled message here)\n"
        f"You can message: {allowed}.\n\n"
        "HOW TO SEND: write your message as a file into outbox/<name>/ (one file per "
        "recipient; read outbox/<name>/about.md first). The moment you have written "
        "your outgoing mail, your TURN IS DONE -- stop and wait. The orchestrator "
        "delivers it and will notify you (a fresh message appears in your inbox and "
        "you'll be nudged) when the recipient replies. Never poll your inbox or run a "
        "loop waiting for a reply; doing so just delays delivery and wedges the swarm."
    )
    if agent.role:
        return agent.role.rstrip() + notice
    return (
        "You are an agent in a multi-agent swarm, but your standing role has not "
        "been set.\n" + notice
    )


def release_next(cfg: SwarmConfig, agent_name: str) -> bool:
    """Release the oldest queued message into *agent_name*'s inbox (one-at-a-time).

    Returns False if the inbox already holds a message (one-at-a-time) or the
    queue is empty; True if a message was moved into the inbox. Each release
    bumps the presentation counter used by the auto-archive fallback.
    """
    agent = cfg.get(agent_name)
    mp = cfg.mail_paths(agent)
    inbox = mp.inbox
    released_id = None
    # The whole read-decide-move runs under ONE per-recipient lock. It used to
    # check the inbox / pick files[0] OUTSIDE the lock (only the move was
    # guarded), so two concurrent release_next(B) calls -- e.g. two agents
    # both stop and release into B, or a hook firing during a supervisor
    # tick -- could each observe an empty inbox, each pick files[0], and
    # either land two messages in one inbox (one-at-a-time breached) or
    # crash with FileNotFoundError when the other already renamed the file
    # away. Serialising the decision under the lock removes the TOCTOU.
    # route_outbound's enqueue already takes this same per-recipient lock,
    # so there is no new lock-ordering edge (see config/supervisor on the
    # queue -> pane -> turn-state discipline).
    with lock.file_lock(cfg, agent_name, "mail"):
        inbox.mkdir(parents=True, exist_ok=True)
        existing = sorted(inbox.iterdir())
        if existing:
            # One-at-a-time: a message is already presented; count it as a presentation.
            _bump_presentations(cfg, agent_name, existing[0].name)
            return False
        q = cfg.queue_dir / agent_name
        # q may not exist yet for an agent that has never received mail;
        # iterdir() would raise, so treat a missing queue as empty.
        files = sorted(f for f in q.iterdir() if f.is_file()) if q.exists() else []
        if not files:
            return False
        oldest = files[0]
        try:
            shutil.move(str(oldest), str(inbox / oldest.name))
        except FileNotFoundError:
            # Another process already released this exact file (we lost the
            # race). The inbox is populated / the file is gone -- there is
            # nothing for us to release, so treat it as "already presented".
            # Swallowing this keeps a lost race from crashing the caller,
            # which -- for the supervisor tick -- would otherwise kill the
            # liveness heartbeat.
            return False
        released_id = oldest.name
    log.log_event(cfg, agent_name, "delivered", id=released_id)
    _set_presentations(cfg, agent_name, released_id, 1)
    return True


def nudge(cfg: SwarmConfig, agent_name: str) -> bool:
    """Re-inject the protocol into *agent_name*'s pane; return ``paste_into``'s result.

    The nudge states the agent's EXACT mailbox paths (the model never assumes
    them) and lists the recipients it is allowed to message. The caller ensures
    the agent is idle first; a paste failure just means we retry on the next tick.
    """
    agent = cfg.get(agent_name)
    mp = cfg.mail_paths(agent)
    allowed = ", ".join(p for p in agent.can_talk_to if p != "system")
    nudge_text = (
        f"You have a new message in {mp.inbox}. Read it and do what it asks.\n"
        f"When you're done, move that file to {mp.read}.\n"
        f"To send a message, write a file into {mp.outbox}/<name>/ "
        f"(read {mp.outbox}/<name>/about.md to see who they are and whether "
        f"they're available). The moment you've written your outgoing mail, your "
        f"TURN IS DONE -- stop and wait; you'll be notified (a new message + nudge) "
        f"when the recipient replies. Do not poll your inbox or wait for the reply "
        f"yourself; that only delays delivery.\n"
        f"You can message: {allowed}."
    )
    try:
        return tmux.paste_into(cfg, agent.session, nudge_text)
    except tmux.SwarmError:
        # Best-effort: if the agent isn't up (or tmux is unavailable) the mail
        # still sits in the queue and gets released on the next sweep / when the
        # agent starts. Never crash a send because a session is missing -- a
        # paste failure just means we retry on the next tick.
        return False


def route_outbound(cfg: SwarmConfig, sender: str, recipient: str, body: str) -> str:
    """Route one outbound message from *sender* to *recipient*. Returns one of
    ``delivered`` / ``bounce`` / ``rate-limited`` / ``user-held``.

    The orchestrator owns all routing/ACL/state; the model only wrote the file.
    """
    sender_agent = cfg.get(sender)
    mp = cfg.mail_paths(sender_agent)

    if recipient == "system":
        # system is never a recipient -- bounce back as mail + drop to failed/.
        system_mail(cfg, sender, "system is not a valid recipient -- your message was not delivered.")
        _move_outbox_file(cfg, sender, "system", body, mp.failed)
        log.log_event(cfg, sender, "bounce", to="system", reason="system-recipient")
        return "bounce"

    if recipient == "user":
        return deliver_to_user(cfg, sender, body)

    if recipient not in cfg.get(sender).can_talk_to:
        allowed = ", ".join(x for x in cfg.get(sender).can_talk_to if x != "system")
        system_mail(
            cfg, sender,
            f"Your message to {recipient} couldn't be sent -- you can message: {allowed}.",
        )
        _move_outbox_file(cfg, sender, recipient, body, mp.failed)
        log.log_event(cfg, sender, "bounce", to=recipient, reason="acl")
        return "bounce"

    if rate_limited(cfg, sender, recipient):
        _move_outbox_file(cfg, sender, recipient, body, mp.failed)
        log.log_event(cfg, sender, "rate-limited", to=recipient)
        return "rate-limited"

    msg_id = new_message_id()
    text = stamp_message(body, sender, recipient, msg_id)
    enqueue(cfg, recipient, text, msg_id)
    _move_outbox_file(cfg, sender, recipient, body, mp.sent)
    log.log_event(cfg, sender, "route", from_=sender, to=recipient, id=msg_id)
    return "delivered"


def on_stop(cfg: SwarmConfig, agent_name: str) -> dict:
    """THE CORE: an agent stopped. Sweep its outbox, route every message, then
    release+nudge every recipient that received mail. Returns a summary dict.

    The orchestrator owns authoritative state, so even a forgetful model that
    never moves mail to ``read/`` can't wedge the swarm.
    """
    agent = cfg.get(agent_name)
    mp = cfg.mail_paths(agent)

    # 1) Snapshot the outbox under a lock, then route OUTSIDE the lock so
    #    enqueue() (which takes its own per-recipient lock) can't deadlock.
    pending = []
    with lock.file_lock(cfg, agent_name, "mail"):
        if mp.outbox.exists():
            for sub in sorted(mp.outbox.iterdir()):
                if not sub.is_dir():
                    continue
                recipient = sub.name
                for f in sorted(sub.iterdir()):
                    if not f.is_file():
                        continue
                    # about.md is the orchestrator-maintained contact card, not
                    # an outbound message -- never route or delete it.
                    if f.name == "about.md":
                        continue
                    pending.append((recipient, f.read_text(), f))

    delivered = bounced = rate_limited_count = 0
    recipients: set[str] = set()

    for recipient, body, f in pending:
        result = route_outbound(cfg, agent_name, recipient, body)
        # route_outbound already moved the file to sent/ or failed/; remove any
        # leftover original so it is never double-routed.
        if f.exists():
            f.unlink()
        if result == "delivered":
            delivered += 1
            if recipient != "user":
                recipients.add(recipient)
        elif result == "user-held":
            delivered += 1
        elif result == "bounce":
            bounced += 1
            recipients.add(agent_name)  # the sender gets the bounce as mail
        elif result == "rate-limited":
            rate_limited_count += 1

    # 2) The turn is finished (clamps busy counters) before we release mail.
    turn.on_turn_finished(cfg, agent_name)

    # 3) Deliver mail to everyone who received/queued some (including the sender,
    #    who may have just been bounced an error). release_next is one-at-a-time
    #    and nudge only fires when a message actually landed.
    for r in sorted(recipients):
        if release_next(cfg, r):
            nudge(cfg, r)

    return {"delivered": delivered, "bounced": bounced, "rate_limited": rate_limited_count}


def process_read_folder(cfg: SwarmConfig, agent_name: str) -> int:
    """Process *agent_name*'s ``read/`` folder: emit read receipts and run the
    auto-archive fallback. Returns the number of new read receipts emitted."""
    agent = cfg.get(agent_name)
    mp = cfg.mail_paths(agent)
    read_dir = mp.read
    state = _load_run_json(cfg, f"{agent_name}.read.json")
    processed = set(state.get("processed", []))
    count = 0

    if read_dir.exists():
        for f in sorted(read_dir.iterdir()):
            if not f.is_file():
                continue
            msg_id = _parse_header_field(f.read_text(), "Id")
            if msg_id is None or msg_id in processed:
                continue
            sender = _parse_header_field(f.read_text(), "From") or "system"
            mark_read(cfg, sender, msg_id)
            processed.add(msg_id)
            log.log_event(cfg, agent_name, "read", id=msg_id)
            count += 1

    state["processed"] = sorted(processed)
    _save_run_json(cfg, f"{agent_name}.read.json", state)

    # Auto-archive fallback (plan §7): a single message presented >= N times
    # without being handled is moved to the archive and the queue advances, so a
    # forgetful model can never wedge the swarm.
    inbox = mp.inbox
    if inbox.exists():
        msgs = sorted(f for f in inbox.iterdir() if f.is_file())
        if len(msgs) == 1:
            f = msgs[0]
            pres = _get_presentations(cfg, agent_name)
            if (
                pres.get("msg_id") == f.name
                and pres.get("count", 0) >= AUTO_ARCHIVE_PRESENTATIONS
                and f.name not in processed
            ):
                log.archive_message(cfg, agent_name, f)
                release_next(cfg, agent_name)

    return count


def maybe_ping(cfg: SwarmConfig, agent_name: str) -> bool:
    """Inject a periodic ``system`` ping into *agent_name*'s queue, respecting
    the three §10 guards. Returns True if a ping was injected.

    Guards: (a) idle-only -- skip if busy; (b) no-pile-up -- skip if an unhandled
    ping marker already sits in the queue/inbox; (c) cadence-is-minimum -- skip
    unless ``periodically_ping_seconds`` has elapsed since the last ping.
    """
    agent = cfg.get(agent_name)
    if agent.periodically_ping_seconds <= 0:
        return False
    if turn.busy_info(cfg, agent) is not None:
        return False

    # (b) no-pile-up: a still-unhandled ping is a file whose name starts with the marker.
    queue = cfg.queue_dir / agent_name
    inbox = cfg.mail_paths(agent).inbox
    for d in (queue, inbox):
        if d.exists():
            for f in d.iterdir():
                if f.name.startswith(PING_MARKER):
                    return False

    # (c) cadence-is-minimum.
    state = _load_run_json(cfg, f"{agent_name}.ping.json")
    last = state.get("last_ping", 0.0)
    now = time.time()
    if now - last < agent.periodically_ping_seconds:
        return False

    msg_id = PING_MARKER + uuid.uuid4().hex[:8]
    text = stamp_message(agent.periodically_ping_message, "system", agent_name, msg_id)
    enqueue(cfg, agent_name, text, msg_id)
    state["last_ping"] = now
    _save_run_json(cfg, f"{agent_name}.ping.json", state)
    log.log_event(cfg, agent_name, "ping")
    return True


def deliver_to_user(cfg: SwarmConfig, sender: str, body: str) -> str:
    """Deliver *sender*'s message into the virtual ``user`` mailbox.

    Returns ``delivered`` (user available), ``user-held`` (user away -- mail is
    held, never bounced, and a ``system`` ack is dropped into the sender), or
    ``bounce`` (sender not allowed to message the user). The sender's outbox
    file is moved to ``sent/`` -- the send itself always succeeds; only the
    human's reply is deferred.
    """
    mp = cfg.mail_paths(cfg.get(sender))
    if "user" not in cfg.get(sender).can_talk_to:
        system_mail(cfg, sender, "You can't message the user -- they're not in your can_talk_to.")
        _move_outbox_file(cfg, sender, "user", body, mp.failed)
        log.log_event(cfg, sender, "bounce", to="user", reason="acl")
        return "bounce"

    msg_id = new_message_id()
    text = stamp_message(body, sender, "user", msg_id)
    enqueue(cfg, "user", text, msg_id)
    _move_outbox_file(cfg, sender, "user", body, mp.sent)
    if cfg.user_available:
        log.log_event(cfg, sender, "delivered", to="user", id=msg_id)
        return "delivered"
    system_mail(cfg, sender, "Delivered -- the user is away and may respond later.")
    log.log_event(cfg, sender, "user-held", to="user", id=msg_id)
    return "user-held"


def send_as_user(cfg: SwarmConfig, to_agent: str, body: str) -> None:
    """The human/UI sends a message FROM ``user`` to *to_agent*."""
    msg_id = new_message_id()
    text = stamp_message(body, "user", to_agent, msg_id)
    enqueue(cfg, to_agent, text, msg_id)
    log.log_event(cfg, to_agent, "user-send", from_="user", id=msg_id)
    if release_next(cfg, to_agent):
        nudge(cfg, to_agent)


def system_mail(cfg: SwarmConfig, to_agent: str, body: str, *, kind: str = "system") -> None:
    """Enqueue a ``system`` message (From: system) into *to_agent*'s queue.

    Used for bounces, acks, and periodic pings -- errors come back as mail so
    the model self-corrects in-band.
    """
    msg_id = new_message_id()
    text = stamp_message(body, "system", to_agent, msg_id)
    enqueue(cfg, to_agent, text, msg_id)
    log.log_event(cfg, to_agent, kind, from_="system")


def set_user_available(cfg: SwarmConfig, available: bool) -> None:
    """Set the user's availability toggle and rewrite the live ``user`` contact cards."""
    cfg.user_available = available
    for b in cfg.agents:
        if "user" in b.can_talk_to:
            mp = cfg.mail_paths(b)
            card = mp.outbox / "user" / "about.md"
            card.parent.mkdir(parents=True, exist_ok=True)
            status = "available" if available else "away"
            card.write_text(f"Name: user\nRole: human operator\nStatus: {status}\n")
    log.log_event(cfg, "user", "user-available" if available else "user-away")
