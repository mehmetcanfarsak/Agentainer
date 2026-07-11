#!/usr/bin/env python3
"""100% line coverage of lib/mail.py -- the v2 file-based MAILROOM.

Every branch of the mail model is exercised directly through the public API,
with tmux/turn/clock faked so no real tmux or API keys are involved. Coverage
of the model's own logic (routing, ACL, queueing, read receipts, auto-archive,
ping guards, rate cap) is the point; the paste layer is mocked where a test
opts in.
"""

import json
import shutil
import time
from pathlib import Path
from unittest import mock

import config as cfgmod
from config import Agent

import log as logmod
import tmux as tmuxmod
import turn as turnmod

import mail as mailmod


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def make_agent(cfg, name, can_talk_to, type="claude", role=None,
               ping_seconds=0, ping_message=""):
    return Agent(
        name=name,
        type=type,
        command="true",
        workdir=cfg.root / name,
        session=f"t-{name}",
        capture="pane",
        boot_delay_ms=0,
        role=role if role is not None else f"role-of-{name}",
        can_talk_to=list(can_talk_to),
        mail_dir=cfg.root / name,
        periodically_ping_seconds=ping_seconds,
        periodically_ping_message=ping_message,
    )


def build_cfg(tmp_runtime, agents):
    tmp_runtime.agents = agents
    return tmp_runtime


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------
# helpers / stamping
# --------------------------------------------------------------------------


def test_new_message_id_format():
    mid = mailmod.new_message_id()
    assert mid.startswith("m-")
    assert len(mid) == 2 + 8  # "m-" + 8 hex


def test_standby_prompt_includes_role_and_paths(tmp_runtime):
    a = make_agent(tmp_runtime, "a", ["b", "system", "user"], role="You are A.")
    text = mailmod.standby_prompt(tmp_runtime, a)
    # role is delivered up front, and the standby notice frames it as no-task-yet.
    assert "You are A." in text
    assert "initialization message" in text
    assert "No task has been assigned to you yet" in text
    assert "do NOT send" in text
    # Principle 3: exact mailbox paths are stated, not assumed.
    mp = tmp_runtime.mail_paths(a)
    assert str(mp.inbox) in text
    assert str(mp.outbox) in text
    assert str(mp.read) in text
    # allowed recipients exclude the reserved 'system' virtual mailbox.
    assert "You can message: b, user" in text
    # The protocol must tell the agent to STOP after sending (pickup is
    # stop-triggered) -- otherwise a model that writes mail and then polls its
    # inbox for the reply holds its own delivery and wedges the swarm.
    assert "TURN IS DONE" in text
    assert "stop and wait" in text
    assert "Never poll your inbox" in text


def test_standby_prompt_roleless_agent(tmp_runtime):
    a = make_agent(tmp_runtime, "a", [], role="")
    text = mailmod.standby_prompt(tmp_runtime, a)
    assert "initialization message" in text
    assert "standing role has not been set" in text
    assert "(no one yet)" in text




def test_format_header_with_and_without_re():
    h = mailmod.format_header("alice", "bob", "m-1", "2026-01-01T00:00:00+00:00")
    assert "From: alice" in h
    assert "To: bob" in h
    assert "Id: m-1" in h
    assert "Time: 2026-01-01T00:00:00+00:00" in h
    assert "Re:" not in h

    h2 = mailmod.format_header("alice", "bob", "m-1", "t", re_="m-0")
    assert "Re: m-0" in h2


def test_stamp_message_combines_header_and_body():
    stamped = mailmod.stamp_message("hello body", "alice", "bob", "m-9")
    assert stamped.startswith("From: alice")
    assert "\n\nhello body" in stamped


# --------------------------------------------------------------------------
# init / contact cards
# --------------------------------------------------------------------------


def test_init_mailboxes_creates_folders_acl_and_cards(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob", "user"])
    bob = make_agent(tmp_runtime, "bob", ["alice"])
    cfg = build_cfg(tmp_runtime, [alice, bob])
    cfg.user_available = False

    mailmod.init_mailboxes(cfg)

    for a in (alice, bob):
        mp = cfg.mail_paths(a)
        for d in (mp.inbox, mp.outbox, mp.read, mp.sent, mp.failed):
            assert d.is_dir(), f"{a.name} missing {d}"
        assert (cfg.queue_dir / a.name).is_dir()

    # ACL folders: alice may talk to bob + user
    assert (cfg.mail_paths(alice).outbox / "bob").is_dir()
    assert (cfg.mail_paths(alice).outbox / "user").is_dir()
    # bob may NOT talk to user -> no user folder
    assert not (cfg.mail_paths(bob).outbox / "user").exists()

    # Contact cards written both directions
    alice_card_for_bob = cfg.mail_paths(bob).outbox / "alice" / "about.md"
    assert alice_card_for_bob.is_file()
    assert "Name: alice" in alice_card_for_bob.read_text()
    assert "Status: available" in alice_card_for_bob.read_text()

    bob_card_for_alice = cfg.mail_paths(alice).outbox / "bob" / "about.md"
    assert "Name: bob" in bob_card_for_alice.read_text()

    # user card reflects availability (away)
    user_card = cfg.mail_paths(alice).outbox / "user" / "about.md"
    assert "Status: away" in user_card.read_text()


def test_write_contact_cards_user_availability(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["user"])
    cfg = build_cfg(tmp_runtime, [alice])
    cfg.user_available = True
    mailmod.write_contact_cards(cfg)
    assert "Status: available" in (cfg.mail_paths(alice).outbox / "user" / "about.md").read_text()

    cfg.user_available = False
    mailmod.write_contact_cards(cfg)
    assert "Status: away" in (cfg.mail_paths(alice).outbox / "user" / "about.md").read_text()


# --------------------------------------------------------------------------
# release_next
# --------------------------------------------------------------------------


def _queue(cfg, agent_name, name, body):
    (cfg.queue_dir / agent_name).mkdir(parents=True, exist_ok=True)
    (cfg.queue_dir / agent_name / name).write_text(body)


def _seed_loop(cfg, pair, data):
    """Pre-seed a per-pair runaway-loop counter (creates run_dir)."""
    mailmod._save_run_json(cfg, f"{pair}.loop.json", data)


def test_release_next_one_at_a_time_occupied(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    # Put a message directly in the inbox.
    inbox = cfg.mail_paths(alice).inbox
    (inbox / "m-stuck.txt").write_text("old")
    assert mailmod.release_next(cfg, "alice") is False
    # presentation counter was bumped
    pres = mailmod._get_presentations(cfg, "alice")
    assert pres == {"msg_id": "m-stuck.txt", "count": 1}


def test_release_next_empty_queue(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    assert mailmod.release_next(cfg, "alice") is False


def test_release_next_releases_oldest(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    _queue(cfg, "alice", "m-second.txt", "two")
    _queue(cfg, "alice", "m-first.txt", "one")  # older name -> released first
    assert mailmod.release_next(cfg, "alice") is True
    inbox = cfg.mail_paths(alice).inbox
    assert (inbox / "m-first.txt").is_file()
    assert not (inbox / "m-second.txt").exists()
    assert "delivered" in [e["kind"] for e in read_jsonl(cfg.log_dir / "alice.jsonl")]
    pres = mailmod._get_presentations(cfg, "alice")
    assert pres == {"msg_id": "m-first.txt", "count": 1}


# --------------------------------------------------------------------------
# nudge
# --------------------------------------------------------------------------


def test_nudge_builds_text_and_pastes(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob", "user"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)

    with mock.patch.object(tmuxmod, "paste_into", return_value=True) as p:
        assert mailmod.nudge(cfg, "alice") is True
        args, kwargs = p.call_args
        assert args[0] is cfg
        assert args[1] == "t-alice"
        text = args[2]
        mp = cfg.mail_paths(alice)
        assert str(mp.inbox) in text
        assert str(mp.read) in text
        assert str(mp.outbox) in text
        assert "bob" in text and "user" in text
        assert "You can message:" in text
        # The nudge must instruct the agent to STOP after sending, so the
        # stop-triggered outbox sweep can deliver the mail (see standby_prompt).
        assert "TURN IS DONE" in text
        assert "Do not poll your inbox" in text

    with mock.patch.object(tmuxmod, "paste_into", return_value=False) as p:
        assert mailmod.nudge(cfg, "alice") is False


# --------------------------------------------------------------------------
# route_outbound branches
# --------------------------------------------------------------------------


def test_route_outbound_system_is_bounce(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    (cfg.mail_paths(alice).outbox / "system").mkdir(parents=True, exist_ok=True)
    (cfg.mail_paths(alice).outbox / "system" / "s.txt").write_text("to system")
    res = mailmod.route_outbound(cfg, "alice", "system", "to system")
    assert res == "bounce"
    # system mail enqueued back to sender
    queued = list((cfg.queue_dir / "alice").iterdir())
    assert len(queued) == 1
    assert "system is not a valid recipient" in queued[0].read_text()
    # original moved to failed
    assert (cfg.mail_paths(alice).failed / "s.txt").is_file()


def test_route_outbound_not_allowed_is_bounce(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])  # carol not allowed
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    (cfg.mail_paths(alice).outbox / "carol").mkdir(parents=True, exist_ok=True)
    (cfg.mail_paths(alice).outbox / "carol" / "c.txt").write_text("hi carol")
    res = mailmod.route_outbound(cfg, "alice", "carol", "hi carol")
    assert res == "bounce"
    queued = list((cfg.queue_dir / "alice").iterdir())
    assert any("couldn't be sent" in f.read_text() for f in queued)
    assert (cfg.mail_paths(alice).failed / "c.txt").is_file()


def test_route_outbound_allowed_delivers(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    (cfg.mail_paths(alice).outbox / "bob" / "m1.txt").write_text("hi bob")
    res = mailmod.route_outbound(cfg, "alice", "bob", "hi bob")
    assert res == "delivered"
    # bob's queue holds the stamped message
    bob_q = list((cfg.queue_dir / "bob").iterdir())
    assert len(bob_q) == 1
    assert "From: alice" in bob_q[0].read_text()
    assert "To: bob" in bob_q[0].read_text()
    # sender's outbox file moved to sent/
    assert (cfg.mail_paths(alice).sent / "m1.txt").is_file()
    assert not (cfg.mail_paths(alice).outbox / "bob" / "m1.txt").exists()


def test_route_outbound_allowed_but_empty_outbox_no_move(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    # no file in outbox/bob -> _take_outbox_file returns None, move is a no-op
    res = mailmod.route_outbound(cfg, "alice", "bob", "hi bob")
    assert res == "delivered"
    assert list((cfg.queue_dir / "bob").iterdir())


def test_route_outbound_rate_limited(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    # pre-seed the loop counter to the cap
    _seed_loop(cfg, "alice-bob", [time.time()] * mailmod.RUNAWAY_CAP)
    (cfg.mail_paths(alice).outbox / "bob" / "m1.txt").write_text("hi bob")
    res = mailmod.route_outbound(cfg, "alice", "bob", "hi bob")
    assert res == "rate-limited"
    bob_q = cfg.queue_dir / "bob"
    assert not bob_q.exists() or not list(bob_q.iterdir())  # not delivered
    assert (cfg.mail_paths(alice).failed / "m1.txt").is_file()


# --------------------------------------------------------------------------
# deliver_to_user
# --------------------------------------------------------------------------


def test_deliver_to_user_available(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["user"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    cfg.user_available = True
    (cfg.mail_paths(alice).outbox / "user" / "u.txt").write_text("hi human")
    res = mailmod.deliver_to_user(cfg, "alice", "hi human")
    assert res == "delivered"
    assert list((cfg.queue_dir / "user").iterdir())
    assert (cfg.mail_paths(alice).sent / "u.txt").is_file()


def test_deliver_to_user_held_when_away(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["user"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    cfg.user_available = False
    (cfg.mail_paths(alice).outbox / "user" / "u.txt").write_text("hi human")
    res = mailmod.deliver_to_user(cfg, "alice", "hi human")
    assert res == "user-held"
    # human-not-reachable ack dropped into sender
    queued = list((cfg.queue_dir / "alice").iterdir())
    assert any("user is away" in f.read_text() for f in queued)
    assert (cfg.mail_paths(alice).sent / "u.txt").is_file()


def test_deliver_to_user_not_allowed(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])  # no user
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    (cfg.mail_paths(alice).outbox / "user").mkdir(parents=True, exist_ok=True)
    (cfg.mail_paths(alice).outbox / "user" / "u.txt").write_text("hi human")
    res = mailmod.deliver_to_user(cfg, "alice", "hi human")
    assert res == "bounce"
    assert (cfg.mail_paths(alice).failed / "u.txt").is_file()


# --------------------------------------------------------------------------
# on_stop (the core)
# --------------------------------------------------------------------------


def test_on_stop_sweeps_allowed_and_user_held(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob", "user"])
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [alice, bob])
    mailmod.init_mailboxes(cfg)
    cfg.user_available = False  # user away -> user-held

    (cfg.mail_paths(alice).outbox / "bob" / "m1.txt").write_text("hi bob")
    (cfg.mail_paths(alice).outbox / "user" / "u1.txt").write_text("hi user")

    with mock.patch.object(tmuxmod, "paste_into", return_value=True) as p:
        res = mailmod.on_stop(cfg, "alice")

    assert res == {"delivered": 2, "bounced": 0, "rate_limited": 0}
    # bob received real mail -> released + nudged
    bob_inbox = list((cfg.mail_paths(bob).inbox).iterdir())
    assert len(bob_inbox) == 1
    assert any(call.args[1] == "t-bob" for call in p.call_args_list)
    # user is virtual -> no nudge for "user"
    assert not any(call.args[1] == "t-user" for call in p.call_args_list)
    # both of alice's outbox files moved to sent/
    assert (cfg.mail_paths(alice).sent / "m1.txt").is_file()
    assert (cfg.mail_paths(alice).sent / "u1.txt").is_file()


def test_on_stop_user_available_delivered_not_nudged(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["user"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    cfg.user_available = True
    (cfg.mail_paths(alice).outbox / "user" / "u1.txt").write_text("hi user")
    with mock.patch.object(tmuxmod, "paste_into", return_value=True) as p:
        res = mailmod.on_stop(cfg, "alice")
    assert res["delivered"] == 1
    # user never gets a paste/nudge
    assert p.call_args_list == []


def test_on_stop_bounce_self_nudges_sender(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])  # carol not allowed
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    (cfg.mail_paths(alice).outbox / "carol").mkdir(parents=True, exist_ok=True)
    (cfg.mail_paths(alice).outbox / "carol" / "c1.txt").write_text("hi carol")
    with mock.patch.object(tmuxmod, "paste_into", return_value=True) as p:
        res = mailmod.on_stop(cfg, "alice")
    assert res == {"delivered": 0, "bounced": 1, "rate_limited": 0}
    # the sender got the bounce as mail, released + nudged
    assert len(list((cfg.mail_paths(alice).inbox).iterdir())) == 1
    assert any(call.args[1] == "t-alice" for call in p.call_args_list)
    assert (cfg.mail_paths(alice).failed / "c1.txt").is_file()


def test_on_stop_rate_limited(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [alice, bob])
    mailmod.init_mailboxes(cfg)
    _seed_loop(cfg, "alice-bob", [time.time()] * mailmod.RUNAWAY_CAP)
    (cfg.mail_paths(alice).outbox / "bob" / "m1.txt").write_text("hi bob")
    res = mailmod.on_stop(cfg, "alice")
    assert res == {"delivered": 0, "bounced": 0, "rate_limited": 1}
    assert (cfg.mail_paths(alice).failed / "m1.txt").is_file()


def test_on_stop_unlinks_leftover_outbox_file(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [alice, bob])
    mailmod.init_mailboxes(cfg)
    (cfg.mail_paths(alice).outbox / "bob" / "m1.txt").write_text("hi bob")
    # route_outbound mocked so it does NOT move the file -> on_stop must unlink it
    with mock.patch.object(mailmod, "route_outbound", return_value="delivered"), \
         mock.patch.object(tmuxmod, "paste_into", return_value=True):
        res = mailmod.on_stop(cfg, "alice")
    assert res["delivered"] == 1
    # leftover original removed
    assert not (cfg.mail_paths(alice).outbox / "bob" / "m1.txt").exists()


# --------------------------------------------------------------------------
# process_read_folder + auto-archive
# --------------------------------------------------------------------------


def test_process_read_folder_emits_receipt(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.init_mailboxes(cfg)
    mp = cfg.mail_paths(bob)
    mp.read.mkdir(parents=True, exist_ok=True)
    body = mailmod.stamp_message("please do X", "alice", "bob", "m-read1")
    (mp.read / "m-read1.txt").write_text(body)

    n = mailmod.process_read_folder(cfg, "bob")
    assert n == 1
    assert (cfg.run_dir / "alice.readreceipts.json").is_file()
    # idempotent: second pass processes nothing new
    assert mailmod.process_read_folder(cfg, "bob") == 0
    # processed list persisted
    assert "m-read1" in mailmod._load_run_json(cfg, "bob.read.json")["processed"]


def test_process_read_folder_corrupt_state_is_safe(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.init_mailboxes(cfg)
    mp = cfg.mail_paths(bob)
    mp.read.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir).mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "bob.read.json").write_text("this is not json")
    (mp.read / "m-r.txt").write_text(mailmod.stamp_message("x", "alice", "bob", "m-r"))
    assert mailmod.process_read_folder(cfg, "bob") == 1


def test_process_read_folder_auto_archive(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.init_mailboxes(cfg)
    mp = cfg.mail_paths(bob)
    mp.inbox.mkdir(parents=True, exist_ok=True)
    (mp.inbox / "m-arc1.txt").write_text(mailmod.stamp_message("old", "alice", "bob", "m-arc1"))
    mailmod._set_presentations(cfg, "bob", "m-arc1.txt", mailmod.AUTO_ARCHIVE_PRESENTATIONS)

    n = mailmod.process_read_folder(cfg, "bob")
    assert n == 0
    # auto-archived out of the inbox
    assert (cfg.runtime / "archive" / "bob" / "m-arc1.txt").is_file()
    assert not (mp.inbox / "m-arc1.txt").exists()


def test_process_read_folder_auto_archive_skips_handled(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.init_mailboxes(cfg)
    mp = cfg.mail_paths(bob)
    mp.inbox.mkdir(parents=True, exist_ok=True)
    (mp.inbox / "m-arc2.txt").write_text(mailmod.stamp_message("old", "alice", "bob", "m-arc2"))
    mailmod._set_presentations(cfg, "bob", "m-arc2.txt", mailmod.AUTO_ARCHIVE_PRESENTATIONS)
    # already handled -> recorded in processed
    mailmod._save_run_json(cfg, "bob.read.json", {"processed": ["m-arc2.txt"]})

    n = mailmod.process_read_folder(cfg, "bob")
    assert n == 0
    # NOT archived
    assert (mp.inbox / "m-arc2.txt").is_file()


# --------------------------------------------------------------------------
# maybe_ping (three guards + success)
# --------------------------------------------------------------------------


def test_maybe_ping_disabled(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [], ping_seconds=0)
    cfg = build_cfg(tmp_runtime, [alice])
    assert mailmod.maybe_ping(cfg, "alice") is False


def test_maybe_ping_busy_guard(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [], ping_seconds=30, ping_message="ping")
    cfg = build_cfg(tmp_runtime, [alice])
    with mock.patch.object(turnmod, "busy_info", return_value={"since": 1}):
        assert mailmod.maybe_ping(cfg, "alice") is False


def test_maybe_ping_pileup_in_queue(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [], ping_seconds=30, ping_message="ping")
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    _queue(cfg, "alice", mailmod.PING_MARKER + "xyz.txt", "old ping")
    assert mailmod.maybe_ping(cfg, "alice") is False


def test_maybe_ping_pileup_in_inbox(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [], ping_seconds=30, ping_message="ping")
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    (cfg.mail_paths(alice).inbox / (mailmod.PING_MARKER + "xyz.txt")).write_text("old ping")
    assert mailmod.maybe_ping(cfg, "alice") is False


def test_maybe_ping_cadence_guard(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [], ping_seconds=10, ping_message="ping")
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod._save_run_json(cfg, "alice.ping.json", {"last_ping": 995.0})
    with mock.patch.object(mailmod, "time") as mtime:
        mtime.time.return_value = 1000.0
        assert mailmod.maybe_ping(cfg, "alice") is False


def test_maybe_ping_success(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [], ping_seconds=10, ping_message="do a thing")
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod._save_run_json(cfg, "alice.ping.json", {"last_ping": 0.0})
    with mock.patch.object(mailmod, "time") as mtime:
        mtime.time.return_value = 1000.0
        assert mailmod.maybe_ping(cfg, "alice") is True
    queued = list((cfg.queue_dir / "alice").iterdir())
    assert len(queued) == 1
    assert queued[0].name.startswith(mailmod.PING_MARKER)
    assert "From: system" in queued[0].read_text()
    assert mailmod._load_run_json(cfg, "alice.ping.json")["last_ping"] == 1000.0
    assert any(e["kind"] == "ping" for e in read_jsonl(cfg.log_dir / "alice.jsonl"))


# --------------------------------------------------------------------------
# system_mail / send_as_user / set_user_available / mark_read / rate_limited
# --------------------------------------------------------------------------


def test_system_mail_enqueues_and_logs(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.system_mail(cfg, "bob", "hello there")
    queued = list((cfg.queue_dir / "bob").iterdir())
    assert len(queued) == 1
    assert "From: system" in queued[0].read_text()
    assert any(e["kind"] == "system" for e in read_jsonl(cfg.log_dir / "bob.jsonl"))


def test_system_mail_custom_kind(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.system_mail(cfg, "bob", "x", kind="ping-ack")
    assert any(e["kind"] == "ping-ack" for e in read_jsonl(cfg.log_dir / "bob.jsonl"))


def test_send_as_user_releases_and_nudges(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.init_mailboxes(cfg)
    with mock.patch.object(tmuxmod, "paste_into", return_value=True) as p:
        mailmod.send_as_user(cfg, "bob", "from human")
    # message was released from the queue into bob's inbox
    bob_inbox = list((cfg.mail_paths(bob).inbox).iterdir())
    assert len(bob_inbox) == 1
    assert "From: user" in bob_inbox[0].read_text()
    # queue is now drained
    assert list((cfg.queue_dir / "bob").iterdir()) == []
    assert any(call.args[1] == "t-bob" for call in p.call_args_list)
    assert any(e["kind"] == "user-send" for e in read_jsonl(cfg.log_dir / "bob.jsonl"))


def test_send_as_user_inbox_occupied_skips_nudge(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.init_mailboxes(cfg)
    # occupy bob's inbox so release_next returns False
    (cfg.mail_paths(bob).inbox / "m-occupied.txt").write_text("busy")
    with mock.patch.object(tmuxmod, "paste_into", return_value=True) as p:
        mailmod.send_as_user(cfg, "bob", "from human")
    assert p.call_args_list == []  # no nudge because nothing was released


def test_set_user_available_rewrites_cards(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["user"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    cfg.user_available = False
    mailmod.set_user_available(cfg, True)
    assert cfg.user_available is True
    assert "Status: available" in (cfg.mail_paths(alice).outbox / "user" / "about.md").read_text()
    assert any(e["kind"] == "user-available" for e in read_jsonl(cfg.log_dir / "user.jsonl"))

    mailmod.set_user_available(cfg, False)
    assert "Status: away" in (cfg.mail_paths(alice).outbox / "user" / "about.md").read_text()
    assert any(e["kind"] == "user-away" for e in read_jsonl(cfg.log_dir / "user.jsonl"))


def test_set_user_available_no_user_references(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    # no agent references user -> card loop is a no-op but event still logged
    mailmod.set_user_available(cfg, True)
    assert any(e["kind"] == "user-available" for e in read_jsonl(cfg.log_dir / "user.jsonl"))


def test_mark_read_records_receipt(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.mark_read(cfg, "alice", "m-xyz")
    assert (cfg.run_dir / "alice.readreceipts.json").is_file()
    assert "m-xyz" in mailmod._load_run_json(cfg, "alice.readreceipts.json")


def test_rate_limited_false_then_true(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    cfg = build_cfg(tmp_runtime, [alice])
    # below cap -> False
    _seed_loop(cfg, "alice-bob", [time.time()] * (mailmod.RUNAWAY_CAP - 1))
    assert mailmod.rate_limited(cfg, "alice", "bob") is False
    # at/over cap -> True
    _seed_loop(cfg, "alice-bob", [time.time()] * mailmod.RUNAWAY_CAP)
    assert mailmod.rate_limited(cfg, "alice", "bob") is True


def test_rate_limited_corrupt_state_safe(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    cfg = build_cfg(tmp_runtime, [alice])
    cfg.run_dir.mkdir(parents=True, exist_ok=True)
    (cfg.run_dir / "alice-bob.loop.json").write_text("not json}}")
    # corrupt file falls back to empty -> not rate limited
    assert mailmod.rate_limited(cfg, "alice", "bob") is False


# --------------------------------------------------------------------------
# coverage of defensive / secondary branches
# --------------------------------------------------------------------------


def test_release_next_rebump_same_message_presentation(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", [])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    _queue(cfg, "alice", "m-a.txt", "a")
    assert mailmod.release_next(cfg, "alice") is True  # releases, count=1
    # inbox now occupied by m-a.txt; a second presentation bumps the SAME msg
    assert mailmod.release_next(cfg, "alice") is False
    assert mailmod._get_presentations(cfg, "alice")["count"] == 2


def test_route_outbound_recipient_folder_absent(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    cfg = build_cfg(tmp_runtime, [alice])
    mailmod.init_mailboxes(cfg)
    shutil.rmtree(cfg.mail_paths(alice).outbox / "bob")
    res = mailmod.route_outbound(cfg, "alice", "bob", "hi bob")
    assert res == "delivered"  # still enqueued; no outbox file to move
    assert list((cfg.queue_dir / "bob").iterdir())


def test_on_stop_skips_non_message_entries(tmp_runtime):
    alice = make_agent(tmp_runtime, "alice", ["bob"])
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [alice, bob])
    mailmod.init_mailboxes(cfg)
    # a stray file directly in the outbox root (not a recipient dir)
    (cfg.mail_paths(alice).outbox / "stray.txt").write_text("x")
    # a sub-directory (non-file) inside a real recipient dir
    (cfg.mail_paths(alice).outbox / "bob" / "asub").mkdir()
    # the one real message
    (cfg.mail_paths(alice).outbox / "bob" / "m1.txt").write_text("hi bob")
    with mock.patch.object(tmuxmod, "paste_into", return_value=True):
        res = mailmod.on_stop(cfg, "alice")
    assert res == {"delivered": 1, "bounced": 0, "rate_limited": 0}


def test_process_read_folder_skips_malformed(tmp_runtime):
    bob = make_agent(tmp_runtime, "bob", [])
    cfg = build_cfg(tmp_runtime, [bob])
    mailmod.init_mailboxes(cfg)
    mp = cfg.mail_paths(bob)
    mp.read.mkdir(parents=True, exist_ok=True)
    (mp.read / "broken.txt").write_text("no header here")
    # a sub-directory inside read/ (not a message file) must be skipped
    (mp.read / "asub").mkdir()
    assert mailmod.process_read_folder(cfg, "bob") == 0
