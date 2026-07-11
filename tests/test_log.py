"""Tests for ``lib/log.py`` -- the durable event log and message archiver.

Targets 100 % line coverage of ``lib/log.py`` using the ``tmp_runtime`` fixture
(a ``SwarmConfig`` with no agents and all runtime dirs under a temp path), so no
real tmux or API keys are involved.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import log  # noqa: E402


def _read_jsonl(path: Path):
    """Return the list of decoded records in a JSONL file (empty if absent)."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_now_iso_format():
    """now_iso returns an ISO-8601 string with second precision."""
    stamp = log.now_iso()
    assert stamp.endswith(("Z", "+00:00")) or "+" in stamp
    # It parses back as a valid ISO timestamp.
    from datetime import datetime

    assert datetime.fromisoformat(stamp) is not None


def test_log_event_writes_both_logs(tmp_runtime):
    """log_event appends identical records to the per-agent and global logs."""
    log.log_event(
        tmp_runtime,
        "lead",
        "queued",
        **{"from": "reviewer"},
        depth=0,
        text="please review",
    )

    per_agent = _read_jsonl(tmp_runtime.log_dir / "lead.jsonl")
    global_log = _read_jsonl(tmp_runtime.log_dir / "agentainer.jsonl")

    assert len(per_agent) == 1
    assert per_agent == global_log  # same record in both

    rec = per_agent[0]
    assert isinstance(rec["ts"], str) and rec["ts"]  # ts present and non-empty
    from datetime import datetime

    assert datetime.fromisoformat(rec["ts"]) is not None
    assert rec["agent"] == "lead"
    assert rec["kind"] == "queued"
    assert rec["from"] == "reviewer"
    assert rec["depth"] == 0
    assert rec["text"] == "please review"


def test_log_event_without_fields(tmp_runtime):
    """log_event works with no extra fields (empty **fields)."""
    log.log_event(tmp_runtime, "solo", "ping")
    recs = _read_jsonl(tmp_runtime.log_dir / "solo.jsonl")
    assert recs == _read_jsonl(tmp_runtime.log_dir / "agentainer.jsonl")
    assert recs[0] == {"ts": recs[0]["ts"], "agent": "solo", "kind": "ping"}


def test_log_event_appends(tmp_runtime):
    """Multiple calls append (do not overwrite) in both logs."""
    log.log_event(tmp_runtime, "a", "x")
    log.log_event(tmp_runtime, "a", "y")
    assert len(_read_jsonl(tmp_runtime.log_dir / "a.jsonl")) == 2
    assert len(_read_jsonl(tmp_runtime.log_dir / "agentainer.jsonl")) == 2


def test_archive_message_moves_file(tmp_runtime):
    """archive_message moves the file into runtime/archive/<agent> and it's gone from src."""
    src = tmp_runtime.root / "msg.md"
    src.write_text("hello world\n\nsecond line\n")
    path = log.archive_message(tmp_runtime, "lead", src)

    assert isinstance(path, Path)
    assert path.exists()
    # Destination is under the agent's archive dir.
    assert path.parent == tmp_runtime.runtime / "archive" / "lead"
    assert path.name == "msg.md"
    # Content is preserved.
    assert path.read_text().rstrip().endswith("second line")
    # Source is gone (it was moved, not copied).
    assert not src.exists()


def test_archive_message_custom_subdir(tmp_runtime):
    """The subdir kwarg relocates the destination (e.g. the 'done' archive)."""
    src = tmp_runtime.root / "m.md"
    src.write_text("x")
    path = log.archive_message(tmp_runtime, "lead", src, subdir="done")
    assert path.parent == tmp_runtime.runtime / "done" / "lead"


def test_archive_message_creates_dir(tmp_runtime):
    """The archive dir is created on demand (does not exist yet)."""
    dest_dir = tmp_runtime.runtime / "archive" / "newagent"
    assert not dest_dir.exists()
    src = tmp_runtime.root / "m.md"
    src.write_text("hi")
    log.archive_message(tmp_runtime, "newagent", src)
    assert dest_dir.is_dir()


def test_archive_message_returns_path_is_file(tmp_runtime):
    """The returned path points at the moved file."""
    src = tmp_runtime.root / "p.md"
    src.write_text("payload")
    path = log.archive_message(tmp_runtime, "a", src)
    assert path.is_file()
