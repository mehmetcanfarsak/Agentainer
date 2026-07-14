#!/usr/bin/env python3
"""Agentainer -- global control-plane registry + shared settings (P5 multi-swarm).

One ``agentainer serve`` manages EVERY swarm on the machine. This module is the
global store that makes that possible, kept deliberately separate from a swarm's
own per-``root`` ``.agentainer/`` runtime:

  * the **registry** (``registry.yaml``) -- the set of known swarms
    (``name`` -> config ``path``), so ``serve`` and the UI can list / open /
    launch them all without the operator passing ``-c`` for each one.
  * **shared settings** (``settings.yaml``) -- machine-wide settings, notably the
    single Telegram bot every swarm shares (a per-swarm ``telegram:`` block still
    overrides it) and the Telegram "active swarm" selector.
  * **scaffolding** -- ``create_swarm()`` writes a fresh ``agentainer.yaml`` (with
    a unique tmux ``session_prefix`` so swarms never collide) and registers it.

Location: ``$AGENTAINER_STATE_DIR`` or ``~/.agentainer``. This is deliberately
NOT ``$AGENTAINER_HOME`` (the code/install root, see ``cli.py``); the two are
distinct concepts.

Zero runtime deps: it reuses ``reconcile.load_raw`` (PyYAML if present, else the
bundled ``minyaml``) for reads and ``reconcile.write_raw`` (the stdlib emitter,
never PyYAML) for writes, so the no-PyYAML path -- the release-gated supported
path -- stays live.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import config as cfgmod  # noqa: E402
import reconcile  # noqa: E402  (load_raw / write_raw: the stdlib YAML I/O)


# --------------------------------------------------------------------------
# locations
# --------------------------------------------------------------------------


def state_dir() -> Path:
    """The global control-plane home: ``$AGENTAINER_STATE_DIR`` or ``~/.agentainer``."""
    raw = os.environ.get("AGENTAINER_STATE_DIR") or "~/.agentainer"
    return Path(os.path.expanduser(raw))


def registry_file() -> Path:
    return state_dir() / "registry.yaml"


def settings_file() -> Path:
    return state_dir() / "settings.yaml"


def swarms_home() -> Path:
    """Where UI/CLI-created swarms live by default (one dir per swarm)."""
    return state_dir() / "swarms"


def examples_dir() -> Path:
    return _LIB.parent / "examples"


# --------------------------------------------------------------------------
# low-level YAML store (a corrupt file must never brick the control plane)
# --------------------------------------------------------------------------


def _read(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return reconcile.load_raw(p) or {}
    except Exception:  # noqa: BLE001 - a hand-corrupted store degrades to empty
        return {}


def _write(path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    reconcile.write_raw(p, data)


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


def load_registry() -> list:
    """The registered swarms as ``[{name, path, added_at}]``.

    Entries whose config file no longer exists are pruned from the returned list
    (and rewritten out on the next mutation), so a deleted swarm can't haunt the
    dashboard forever.
    """
    data = _read(registry_file())
    entries = data.get("swarms") or []
    kept = [
        e for e in entries
        if isinstance(e, dict) and e.get("path") and Path(str(e["path"])).is_file()
    ]
    return kept


def save_registry(entries: list) -> None:
    _write(registry_file(), {"swarms": entries})


def register(name: str, path) -> None:
    """Add (or refresh) a swarm. Idempotent: dedupes by both name and resolved path."""
    resolved = str(Path(path).expanduser().resolve())
    entries = [
        e for e in load_registry()
        if e.get("name") != name and str(Path(str(e["path"])).resolve()) != resolved
    ]
    entries.append({"name": name, "path": resolved, "added_at": int(time.time())})
    save_registry(entries)


def unregister(name: str) -> bool:
    """Forget a swarm (config files are left on disk). Returns True if removed."""
    entries = load_registry()
    kept = [e for e in entries if e.get("name") != name]
    if len(kept) == len(entries):
        return False
    save_registry(kept)
    return True


def list_entries() -> list:
    return load_registry()


def entry(name: str) -> dict | None:
    for e in load_registry():
        if e.get("name") == name:
            return e
    return None


def resolve(name: str) -> "cfgmod.SwarmConfig":
    """Load the ``SwarmConfig`` for a registered swarm (raises ``KeyError``)."""
    e = entry(name)
    if e is None:
        raise KeyError(name)
    return cfgmod.load(e["path"])


def load_all() -> dict:
    """``name -> SwarmConfig`` for every registered swarm that loads cleanly.

    A swarm whose YAML is currently invalid is skipped rather than aborting the
    whole control plane -- ``serve`` must survive one broken config.
    """
    out: dict = {}
    for e in load_registry():
        try:
            cfg = cfgmod.load(e["path"])
        except Exception:  # noqa: BLE001 - one broken swarm must not sink serve
            continue
        out[e.get("name") or cfg.name] = cfg
    return out


# --------------------------------------------------------------------------
# shared settings (machine-wide; e.g. the single Telegram bot)
# --------------------------------------------------------------------------


def load_settings() -> dict:
    return _read(settings_file())


def save_settings(data: dict) -> None:
    _write(settings_file(), data)


def global_telegram() -> dict:
    """The shared Telegram block, or ``{}`` when unset."""
    tg = load_settings().get("telegram")
    return dict(tg) if isinstance(tg, dict) else {}


def set_global_telegram(**fields) -> dict:
    """Merge *fields* into the shared Telegram block and persist it."""
    s = load_settings()
    tg = dict(s.get("telegram")) if isinstance(s.get("telegram"), dict) else {}
    tg.update(fields)
    s["telegram"] = tg
    save_settings(s)
    return tg


def active_swarm() -> str | None:
    """The Telegram-selected swarm (which swarm bare ``/commands`` target)."""
    return load_settings().get("active_swarm") or None


def set_active_swarm(name: str) -> None:
    s = load_settings()
    s["active_swarm"] = name
    save_settings(s)


# --------------------------------------------------------------------------
# scaffolding a brand-new swarm
# --------------------------------------------------------------------------


def list_examples() -> list:
    """Bundled example swarm names (``examples/*.yaml`` stems), sorted."""
    return sorted(p.stem for p in examples_dir().glob("*.yaml"))


def example_raw(template: str) -> dict:
    """Parse a bundled example into a plain dict (raises ``ValueError`` if unknown)."""
    p = examples_dir() / f"{template}.yaml"
    if not p.is_file():
        raise ValueError(f"unknown template: {template!r}")
    return reconcile.load_raw(p) or {}


def create_swarm(
    name: str,
    root: str | None = None,
    session_prefix: str | None = None,
    template: str | None = None,
    dest: str | None = None,
    raw: dict | None = None,
) -> Path:
    """Scaffold a fresh ``agentainer.yaml`` and register it; return its path.

    * ``template`` seeds ``defaults:``/``agents:`` from a bundled example
      (``examples/<template>.yaml``); the swarm's identity (name, unique
      ``session_prefix``, root) always overrides the template's.
    * ``raw`` (mutually exclusive with ``template``) supplies a complete config
      dict verbatim (used when the UI hands back operator-edited YAML); its
      ``swarm:`` identity is still normalised so name/prefix stay consistent.
    * neither -> an empty ``agents: []`` swarm (valid; ``up`` brings up just the
      runtime + UI and agents are added later).

    The written file is validated by loading it; an invalid scaffold raises and
    nothing is registered.
    """
    name = str(name).strip()
    if not name or not cfgmod.NAME_RE.match(name):
        raise ValueError(
            f"invalid swarm name: {name!r} (must match {cfgmod.NAME_RE.pattern})"
        )
    if entry(name) is not None:
        raise ValueError(f"a swarm named {name!r} is already registered")

    dest_dir = Path(dest).expanduser() if dest else swarms_home() / name
    cfg_path = dest_dir / "agentainer.yaml"
    if cfg_path.exists():
        raise ValueError(f"config already exists: {cfg_path}")

    prefix = session_prefix if session_prefix is not None else f"{name}_"
    identity = {"name": name, "session_prefix": prefix, "root": root or "./workspace"}

    if template and raw:
        raise ValueError("pass template OR raw, not both")
    if template:
        data = example_raw(template)
    elif raw is not None:
        data = dict(raw)
    else:
        data = {"agents": []}
    swarm_block = dict(data.get("swarm") or {})
    swarm_block.update(identity)
    data["swarm"] = swarm_block

    dest_dir.mkdir(parents=True, exist_ok=True)
    reconcile.write_raw(cfg_path, data)
    try:
        cfgmod.load(cfg_path)  # validate before we register a broken swarm
    except Exception:
        cfg_path.unlink(missing_ok=True)
        raise
    register(name, cfg_path)
    return cfg_path
