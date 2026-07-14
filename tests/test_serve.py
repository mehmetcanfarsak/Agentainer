"""100% line-coverage tests for ``cli.cmd_serve`` (the UI control-plane command).

The UI server itself is covered in ``tests/test_ui.py``; here we only exercise the
CLI handler: token resolution (explicit / env / generated), the loopback default
host, and the KeyboardInterrupt-driven shutdown path.
"""

import pytest

import cli
from support import load_swarm

GENERAL_AGENTS = """
  - name: orchestrator
    type: claude
    capture: none
    can_talk_to: "*"
    role: "You are the orchestrator."
  - name: worker
    type: gemini
    capture: none
    can_talk_to: [orchestrator, user]
    role: ""
"""


class _FakeHandle:
    url = "http://127.0.0.1:0"


def _raise_kb(*a, **k):
    raise KeyboardInterrupt()


def _serve_capture(monkeypatch, tmp_path, token=None, env=None):
    captured = {}

    def fake_run(cfg=None, token="", host="127.0.0.1", port=0, background=True,
                 ui_dir=None, swarms=None):
        captured["token"] = token
        captured["host"] = host
        captured["port"] = port
        captured["swarms"] = swarms
        h = _FakeHandle()
        h.shut = False
        h.shutdown = lambda: captured.update(shut=True)
        return h

    monkeypatch.setattr(cli.ui, "run_server", fake_run)
    monkeypatch.setattr(cli.time, "sleep", _raise_kb)
    cfg = load_swarm(tmp_path, GENERAL_AGENTS)
    argv = ["serve", "-c", str(cfg.path)]
    if token is not None:
        argv += ["--token", token]
    if env is not None:
        monkeypatch.setenv("AGENTAINER_UI_TOKEN", env)
    rc = cli.main(argv)
    return rc, captured


def test_serve_explicit_token(monkeypatch, tmp_path):
    rc, cap = _serve_capture(monkeypatch, tmp_path, token="s3cret")
    assert rc == 0
    assert cap["token"] == "s3cret"
    assert cap["shut"] is True
    # `serve -c <path>` folds that swarm into the multi-swarm live set.
    assert cap["swarms"] and "agentainer" in cap["swarms"]


def test_serve_token_from_env(monkeypatch, tmp_path):
    rc, cap = _serve_capture(monkeypatch, tmp_path, env="envtok")
    assert rc == 0
    assert cap["token"] == "envtok"


def test_serve_generated_token(monkeypatch, tmp_path):
    rc, cap = _serve_capture(monkeypatch, tmp_path)
    assert rc == 0
    assert len(cap["token"]) == 32  # secrets.token_hex(16) -> 32 hex chars


def test_serve_default_host_loopback(monkeypatch, tmp_path):
    rc, cap = _serve_capture(monkeypatch, tmp_path, token="t")
    assert rc == 0
    assert cap["host"] == "127.0.0.1"


# --------------------------------------------------------------------------
# `user` nested subcommands must accept -c (regression: they didn't)
# --------------------------------------------------------------------------


def test_user_inbox_with_config(monkeypatch, tmp_path, capsys):
    cfg = load_swarm(tmp_path, GENERAL_AGENTS)
    rc = cli.main(["user", "inbox", "-c", str(cfg.path)])
    assert rc == 0  # empty mailbox -> "user: no mail", exit 0


def test_user_send_with_config(monkeypatch, tmp_path):
    cfg = load_swarm(tmp_path, GENERAL_AGENTS)
    rc = cli.main(["user", "send", "-c", str(cfg.path), "--to", "orchestrator", "hi there"])
    assert rc == 0
    # The operator's message lands in the recipient's inbox queue.
    inbox = cfg.mail_paths(cfg.get("orchestrator")).inbox
    assert any(inbox.iterdir())


# --------------------------------------------------------------------------
# `agentainer mcp` -- the stdio MCP transport command
# --------------------------------------------------------------------------


def test_cmd_mcp_runs_stdio(monkeypatch):
    called = {}

    def fake_stdio():
        called["ran"] = True
        return 0

    monkeypatch.setattr(cli.mcpmod, "serve_stdio", fake_stdio)
    rc = cli.main(["mcp"])
    assert rc == 0
    assert called["ran"] is True
