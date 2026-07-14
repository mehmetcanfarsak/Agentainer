"""100% line-coverage tests for lib/registry.py (the global control-plane store).

The autouse ``_isolate_state_dir`` fixture (tests/conftest.py) points
``AGENTAINER_STATE_DIR`` at a throwaway dir per test, so the registry / settings
never touch the developer's real ``~/.agentainer`` and each test starts clean.
"""

import os
import sys
from pathlib import Path

import pytest

import config as cfgmod
import registry


# --------------------------------------------------------------------------
# locations
# --------------------------------------------------------------------------


def test_state_dir_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTAINER_STATE_DIR", str(tmp_path / "sd"))
    assert registry.state_dir() == tmp_path / "sd"


def test_state_dir_default_is_home(monkeypatch):
    monkeypatch.delenv("AGENTAINER_STATE_DIR", raising=False)
    assert registry.state_dir() == Path(os.path.expanduser("~/.agentainer"))


def test_location_helpers():
    sd = registry.state_dir()
    assert registry.registry_file() == sd / "registry.yaml"
    assert registry.settings_file() == sd / "settings.yaml"
    assert registry.swarms_home() == sd / "swarms"
    assert registry.examples_dir() == registry._LIB.parent / "examples"


# --------------------------------------------------------------------------
# low-level store: a corrupt file degrades to {}
# --------------------------------------------------------------------------


def test_read_missing_returns_empty():
    assert registry._read(registry.registry_file()) == {}


def test_read_corrupt_registry_returns_empty():
    p = registry.registry_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{unclosed")  # unparseable YAML -> load_raw raises -> {}
    assert registry._read(p) == {}
    # and the public reader tolerates it too
    assert registry.load_registry() == []


def test_read_corrupt_settings_returns_empty():
    p = registry.settings_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{unclosed")
    assert registry.load_settings() == {}
    assert registry.global_telegram() == {}
    assert registry.active_swarm() is None


# --------------------------------------------------------------------------
# registry: register / entry / resolve / load_all / prune / unregister
# --------------------------------------------------------------------------


def _write_cfg(tmp_path, name, body=None):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "agentainer.yaml"
    p.write_text(
        body
        or (
            f"swarm: {{name: {name}, root: ./ws, session_prefix: '{name}_'}}\n"
            "defaults: {type: claude}\n"
            "agents:\n  - {name: a, command: 'true', can_talk_to: []}\n"
        )
    )
    return p


def test_register_and_entry_and_list(tmp_path):
    p = _write_cfg(tmp_path, "alpha")
    registry.register("alpha", p)
    e = registry.entry("alpha")
    assert e["name"] == "alpha"
    assert e["path"] == str(Path(p).resolve())
    assert "added_at" in e
    assert [x["name"] for x in registry.list_entries()] == ["alpha"]
    # unknown name -> None
    assert registry.entry("ghost") is None


def test_register_dedupes_by_name_and_path(tmp_path):
    p1 = _write_cfg(tmp_path, "alpha")
    p2 = _write_cfg(tmp_path, "beta")
    registry.register("alpha", p1)
    # same name, different path -> the old name entry is dropped, only one remains
    registry.register("alpha", p2)
    entries = registry.list_entries()
    assert len(entries) == 1
    assert entries[0]["path"] == str(Path(p2).resolve())
    # same path, different name -> the old path entry is dropped
    registry.register("gamma", p2)
    entries = registry.list_entries()
    assert len(entries) == 1
    assert entries[0]["name"] == "gamma"


def test_load_registry_prunes_missing_files(tmp_path):
    p = _write_cfg(tmp_path, "alpha")
    registry.register("alpha", p)
    assert registry.list_entries()  # present
    p.unlink()  # config file deleted out from under us
    assert registry.load_registry() == []  # pruned


def test_resolve_returns_config_and_raises(tmp_path):
    p = _write_cfg(tmp_path, "alpha")
    registry.register("alpha", p)
    cfg = registry.resolve("alpha")
    assert isinstance(cfg, cfgmod.SwarmConfig)
    assert cfg.name == "alpha"
    with pytest.raises(KeyError):
        registry.resolve("ghost")


def test_load_all_skips_broken_config(tmp_path):
    good = _write_cfg(tmp_path, "good")
    bad = _write_cfg(
        tmp_path,
        "bad",
        body=(
            "swarm: {name: bad, root: ./ws}\n"
            "defaults: {type: claude}\n"
            # can_talk_to references a nonexistent peer -> ConfigError on load
            "agents:\n  - {name: a, command: 'true', can_talk_to: [ghost]}\n"
        ),
    )
    registry.register("good", good)
    registry.register("bad", bad)
    loaded = registry.load_all()
    assert set(loaded) == {"good"}  # the broken one is skipped, not fatal
    assert isinstance(loaded["good"], cfgmod.SwarmConfig)


def test_load_all_uses_config_name_when_entry_name_blank(tmp_path):
    # entry name falls back to cfg.name when the registered name is empty.
    p = _write_cfg(tmp_path, "alpha")
    registry.save_registry([{"name": "", "path": str(p.resolve()), "added_at": 1}])
    loaded = registry.load_all()
    assert "alpha" in loaded


def test_unregister(tmp_path):
    p = _write_cfg(tmp_path, "alpha")
    registry.register("alpha", p)
    assert registry.unregister("alpha") is True
    assert registry.list_entries() == []
    # removing something not present -> False
    assert registry.unregister("alpha") is False


# --------------------------------------------------------------------------
# shared settings + telegram + active swarm
# --------------------------------------------------------------------------


def test_settings_round_trip():
    assert registry.load_settings() == {}
    registry.save_settings({"foo": "bar"})
    assert registry.load_settings() == {"foo": "bar"}


def test_global_telegram_set_and_merge():
    assert registry.global_telegram() == {}
    # the in-memory return reflects exactly what was passed...
    tg = registry.set_global_telegram(bot_token="1:x", chat_id="c9")
    assert tg == {"bot_token": "1:x", "chat_id": "c9"}
    # ...and it persists + reloads through the YAML store.
    assert registry.global_telegram() == {"bot_token": "1:x", "chat_id": "c9"}
    # merge keeps prior fields, updates/adds new ones
    tg2 = registry.set_global_telegram(enabled=True, chat_id="c10")
    assert tg2 == {"bot_token": "1:x", "chat_id": "c10", "enabled": True}
    assert registry.global_telegram()["enabled"] is True


def test_global_telegram_ignores_non_dict():
    registry.save_settings({"telegram": "nope"})
    assert registry.global_telegram() == {}
    # set_global_telegram starts fresh when the stored value isn't a mapping
    tg = registry.set_global_telegram(enabled=True)
    assert tg == {"enabled": True}


def test_active_swarm_set_and_get():
    assert registry.active_swarm() is None
    registry.set_active_swarm("alpha")
    assert registry.active_swarm() == "alpha"
    # an empty stored value degrades to None
    registry.set_active_swarm("")
    assert registry.active_swarm() is None


# --------------------------------------------------------------------------
# scaffolding: list_examples / example_raw / create_swarm
# --------------------------------------------------------------------------


def test_list_examples_includes_bundled():
    names = registry.list_examples()
    assert "research" in names
    assert names == sorted(names)


def test_example_raw_known_and_unknown():
    raw = registry.example_raw("research")
    assert isinstance(raw, dict)
    assert raw.get("agents")
    with pytest.raises(ValueError):
        registry.example_raw("does-not-exist")


def test_create_swarm_empty(tmp_path):
    path = registry.create_swarm("myswarm")
    assert path.is_file()
    assert path == registry.swarms_home() / "myswarm" / "agentainer.yaml"
    cfg = cfgmod.load(path)
    assert cfg.name == "myswarm"
    assert cfg.session_prefix == "myswarm_"
    assert cfg.agents == []
    # it was registered
    assert registry.entry("myswarm") is not None


def test_create_swarm_with_dest_and_prefix_and_root(tmp_path):
    dest = tmp_path / "custom"
    path = registry.create_swarm(
        "custom1", root=str(tmp_path / "wsroot"), session_prefix="cx-", dest=str(dest)
    )
    assert path == dest / "agentainer.yaml"
    cfg = cfgmod.load(path)
    assert cfg.session_prefix == "cx-"


def test_create_swarm_from_template(tmp_path):
    path = registry.create_swarm("res1", template="research")
    cfg = cfgmod.load(path)
    assert cfg.name == "res1"  # identity overrides the template's own name
    assert cfg.agents  # seeded from the template


def test_create_swarm_from_raw_dict(tmp_path):
    raw = {
        "defaults": {"type": "claude"},
        "agents": [{"name": "solo", "command": "true", "can_talk_to": []}],
    }
    path = registry.create_swarm("rawswarm", raw=raw)
    cfg = cfgmod.load(path)
    assert cfg.names() == ["solo"]
    assert cfg.name == "rawswarm"


def test_create_swarm_bad_name():
    with pytest.raises(ValueError):
        registry.create_swarm("bad name!")
    with pytest.raises(ValueError):
        registry.create_swarm("   ")


def test_create_swarm_duplicate_name(tmp_path):
    registry.create_swarm("dup")
    with pytest.raises(ValueError):
        registry.create_swarm("dup")


def test_create_swarm_existing_config_path(tmp_path):
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "agentainer.yaml").write_text("swarm: {}\n")
    with pytest.raises(ValueError):
        registry.create_swarm("occ", dest=str(dest))


def test_create_swarm_template_and_raw_conflict():
    with pytest.raises(ValueError):
        registry.create_swarm("both", template="research", raw={"agents": []})


def test_create_swarm_invalid_raw_cleans_up_and_does_not_register(tmp_path):
    # A raw dict that produces an INVALID config: the written file must be
    # unlinked and nothing registered.
    raw = {
        "defaults": {"type": "claude"},
        "agents": [{"name": "a", "command": "true", "can_talk_to": ["ghost"]}],
    }
    with pytest.raises(cfgmod.ConfigError):
        registry.create_swarm("invalid1", raw=raw)
    assert registry.entry("invalid1") is None
    assert not (registry.swarms_home() / "invalid1" / "agentainer.yaml").exists()


# --------------------------------------------------------------------------
# import-time sys.path guard (line 36)
# --------------------------------------------------------------------------


def test_registry_inserts_lib_path_when_missing():
    import importlib.util

    saved = sys.path[:]
    original = sys.modules.get("registry")
    sys.path = [p for p in sys.path if p != str(registry._LIB)]
    sys.modules.pop("registry", None)
    try:
        spec = importlib.util.spec_from_file_location(
            "registry", str(registry._LIB / "registry.py")
        )
        fresh = importlib.util.module_from_spec(spec)
        sys.modules["registry"] = fresh
        spec.loader.exec_module(fresh)
        assert str(registry._LIB) in sys.path
    finally:
        sys.path[:] = saved
        if original is not None:
            sys.modules["registry"] = original
        else:
            sys.modules.pop("registry", None)
