"""Tests for lib/materialize.py -- per-agent coding-agent config materialisation.

Builds a config via the real loader (so validation/merge is exercised too), then
asserts the concrete files each CLI reads land in the workdir with the right
content and merge behaviour. No tmux / API keys.
"""

import json

import pytest

import config as cfgmod
import materialize
import reconcile


def _cfg(tmp_path, atype, agent_extra, defaults=None, agent_types=None):
    raw = {
        "swarm": {"name": "t", "root": str(tmp_path / "ws"), "session_prefix": "t_"},
        "defaults": {"type": atype, **(defaults or {})},
        "agents": [dict(name="dev", can_talk_to=["user"], **agent_extra)],
    }
    if agent_types:
        raw["agent_types"] = agent_types
    path = tmp_path / "agentainer.yaml"
    reconcile.write_raw(path, raw)
    cfg = cfgmod.load(path)
    a = cfg.get("dev")
    a.workdir.mkdir(parents=True, exist_ok=True)
    return cfg, a


def _skill(tmp_path, name="reviewer"):
    d = tmp_path / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("do the thing")
    return d


# ---- claude (fully supported) --------------------------------------------


def test_claude_full(tmp_path):
    sk = _skill(tmp_path)
    cfg, a = _cfg(tmp_path, "claude", {
        "context": "You are dev.\nBe careful.",
        "mcp": {"gh": {"command": "npx", "args": ["-y", "g"], "env": {"T": "x"}}},
        "settings": {"model": "opus"},
        "skills": [str(sk)],
        "files": {"docs/notes.md": "line1\nline2"},
    })
    wd = a.workdir
    # pre-existing hook settings must survive the settings merge
    (wd / ".claude").mkdir(parents=True, exist_ok=True)
    (wd / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [1]}}))

    assert materialize.apply(cfg, a) == []
    assert (wd / "CLAUDE.md").read_text() == "You are dev.\nBe careful.\n"
    assert json.loads((wd / ".mcp.json").read_text())["mcpServers"]["gh"]["command"] == "npx"
    s = json.loads((wd / ".claude" / "settings.json").read_text())
    assert s["model"] == "opus" and "hooks" in s  # merged, not clobbered
    assert (wd / ".claude" / "skills" / "reviewer" / "SKILL.md").exists()
    assert (wd / "docs" / "notes.md").read_text() == "line1\nline2"
    # re-materialising replaces the skill dir (idempotent copy over an existing one)
    materialize.apply(cfg, a)
    assert (wd / ".claude" / "skills" / "reviewer" / "SKILL.md").exists()


def test_claude_mcp_merges_existing(tmp_path):
    cfg, a = _cfg(tmp_path, "claude", {"mcp": {"new": {"command": "n"}}})
    wd = a.workdir
    (wd / ".mcp.json").write_text(json.dumps({"mcpServers": {"old": {"command": "o"}}, "other": 1}))
    materialize.apply(cfg, a)
    data = json.loads((wd / ".mcp.json").read_text())
    assert set(data["mcpServers"]) == {"old", "new"}
    assert data["other"] == 1  # unrelated keys preserved


def test_claude_corrupt_json_warns(tmp_path):
    cfg, a = _cfg(tmp_path, "claude", {"mcp": {"gh": {"command": "x"}}})
    (a.workdir / ".mcp.json").write_text("{not json")
    warnings = materialize.apply(cfg, a)
    assert any("not valid JSON" in w for w in warnings)
    assert "gh" in json.loads((a.workdir / ".mcp.json").read_text())["mcpServers"]


def test_claude_idempotent(tmp_path):
    cfg, a = _cfg(tmp_path, "claude", {"mcp": {"gh": {"command": "x"}}})
    materialize.apply(cfg, a)
    materialize.apply(cfg, a)
    assert list(json.loads((a.workdir / ".mcp.json").read_text())["mcpServers"]) == ["gh"]


# ---- codex (context + files + toml mcp; settings/skills unsupported) -------


def test_codex_mcp_toml_appends_and_dedupes(tmp_path):
    cfg, a = _cfg(tmp_path, "codex", {
        "mcp": {"gh": {"command": "npx", "args": ["a"], "timeout": 30, "on": True,
                       "env": {"TOKEN": "secret"}}},
        "context": "ctx", "settings": {"x": 1}, "skills": [str(_skill(tmp_path))],
    })
    wd = a.workdir
    (wd / ".codex").mkdir(parents=True, exist_ok=True)
    (wd / ".codex" / "config.toml").write_text('notify = ["/n.sh"]\n')
    warnings = materialize.apply(cfg, a)
    toml = (wd / ".codex" / "config.toml").read_text()
    assert "notify" in toml and "[mcp_servers.gh]" in toml
    assert "[mcp_servers.gh.env]" in toml and 'TOKEN = "secret"' in toml
    assert (wd / "AGENTS.md").read_text().strip() == "ctx"
    # settings + skills unsupported for codex -> warned
    assert any("settings" in w for w in warnings)
    assert any("skills" in w for w in warnings)
    # re-run keeps exactly one mcp table (notify preserved)
    materialize.apply(cfg, a)
    toml2 = (wd / ".codex" / "config.toml").read_text()
    assert toml2.count("[mcp_servers.gh]") == 1 and "notify" in toml2


def test_codex_invalid_toml_skipped(tmp_path, monkeypatch):
    cfg, a = _cfg(tmp_path, "codex", {"mcp": {"gh": {"command": "x"}}})
    import hooks
    monkeypatch.setattr(hooks, "valid_toml", lambda t: False)
    warnings = materialize.apply(cfg, a)
    assert any("invalid" in w for w in warnings)
    assert not (a.workdir / ".codex" / "config.toml").exists()


# ---- gemini (mcp + settings share one file) --------------------------------


def test_gemini_mcp_and_settings_same_file(tmp_path):
    cfg, a = _cfg(tmp_path, "gemini", {
        "mcp": {"g": {"command": "x"}}, "settings": {"theme": "dark"},
        "skills": [str(_skill(tmp_path))],
    })
    warnings = materialize.apply(cfg, a)
    data = json.loads((a.workdir / ".gemini" / "settings.json").read_text())
    assert data["mcpServers"]["g"]["command"] == "x" and data["theme"] == "dark"
    assert any("skills" in w for w in warnings)  # skills claude-only


# ---- hermes / unknown type (context + files only) --------------------------


def test_hermes_mcp_unsupported(tmp_path):
    cfg, a = _cfg(tmp_path, "hermes", {
        "type": "hermes", "command": "hermes",
        "mcp": {"g": {"command": "x"}},
    })
    warnings = materialize.apply(cfg, a)
    assert any("no known config location" in w for w in warnings)


def test_unknown_type_uses_default_profile(tmp_path):
    cfg, a = _cfg(
        tmp_path, "myllm",
        {"context": "hi", "mcp": {"g": {"command": "x"}}, "files": {"a.txt": "b"}},
        agent_types={"myllm": {"command": "myllm-cli"}},
    )
    warnings = materialize.apply(cfg, a)
    assert (a.workdir / "AGENTS.md").read_text().strip() == "hi"
    assert (a.workdir / "a.txt").read_text() == "b"
    assert any("mcp" in w for w in warnings)


def test_apply_noop_when_empty(tmp_path):
    cfg, a = _cfg(tmp_path, "claude", {})
    assert materialize.apply(cfg, a) == []


# ---- helpers --------------------------------------------------------------


def test_strip_toml_section():
    text = 'notify = ["x"]\n\n[mcp_servers.a]\ncommand = "z"\n\n[projects.p]\ntrust = 1\n'
    out = materialize._strip_toml_section(text, "mcp_servers")
    assert "notify" in out and "[projects.p]" in out and "mcp_servers" not in out


def test_toml_scalar_types():
    assert materialize._toml_scalar(True) == "true"
    assert materialize._toml_scalar(3) == "3"
    assert materialize._toml_scalar(["a", 1]) == '["a", 1]'
    assert materialize._toml_scalar("x") == '"x"'


def test_sys_path_guard_inserts():
    import importlib.util
    import sys

    lib_dir = str(materialize._LIB)
    saved = list(sys.path)
    sys.path = [p for p in sys.path if p != lib_dir]
    try:
        spec = importlib.util.spec_from_file_location("_materialize_guard_probe", materialize.__file__)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    assert "claude" in mod.PROFILE
