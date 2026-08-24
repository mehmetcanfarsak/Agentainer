#!/usr/bin/env python3
"""Agentainer -- materialise per-agent coding-agent config into the workdir.

An ``agentainer.yaml`` agent may declare its own coding-agent configuration --
MCP servers, a project-context file, Claude skills, CLI settings, and arbitrary
extra files -- so a swarm ships fully configured from a single YAML. This module
turns those declarations into the concrete files each CLI reads, laid down in the
agent's workdir just before launch.

It is **type-aware**: the same logical `context:` becomes ``CLAUDE.md`` for a
claude agent, ``AGENTS.md`` for codex, ``GEMINI.md`` for gemini; `mcp:` becomes
``.mcp.json`` (claude), a ``.gemini/settings.json`` block (gemini), or
``[mcp_servers.*]`` tables in ``.codex/config.toml`` (codex). Claude is the
fully-supported type; for the others, `context`/`files` always work and the rest
is best-effort, emitting an honest warning where a feature has no home.

Design rules (mirrors the rest of lib/):
  * Zero runtime deps (json is stdlib; a tiny TOML emitter avoids needing tomllib
    for *writing*). Reuses ``hooks.valid_toml`` for a sanity check when present.
  * **Merge, never clobber.** ``.claude/settings.json`` already holds the Stop /
    SessionStart hooks (installed by ``hooks.install_claude_hook`` just before we
    run), so settings are merged into it, not overwritten. Same for ``.mcp.json``
    and codex's ``config.toml``.
  * Best-effort per item: a single bad entry warns and is skipped; it never wedges
    ``up``. Idempotent -- safe to re-run on every launch / resume.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))


# Per-type profile: where each logical config lands. A ``None`` slot means the
# feature has no known home for that CLI (warned + skipped). ``mcp``/``settings``
# json slots are ``(relative_path, top_level_key_or_None)``.
PROFILE: dict[str, dict] = {
    "claude": {
        "context": "CLAUDE.md",
        "mcp": ("json", ".mcp.json", "mcpServers"),
        "settings": ("json", ".claude/settings.json", None),
        "skills": ".claude/skills",
    },
    "codex": {
        "context": "AGENTS.md",
        "mcp": ("toml", ".codex/config.toml", "mcp_servers"),
        "settings": None,
        "skills": None,
    },
    "gemini": {
        "context": "GEMINI.md",
        "mcp": ("json", ".gemini/settings.json", "mcpServers"),
        "settings": ("json", ".gemini/settings.json", None),
        "skills": None,
    },
    "hermes": {
        "context": "AGENTS.md",
        "mcp": None,
        "settings": None,
        "skills": None,
    },
}

# Fallback for a custom/unknown ``type`` (agent_types:): context + files only.
_DEFAULT_PROFILE = {"context": "AGENTS.md", "mcp": None, "settings": None, "skills": None}


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _load_json(path: Path, warnings: list, label: str) -> dict:
    """Read an existing JSON object, or ``{}``; a corrupt file warns and is replaced."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        warnings.append(f"{label}: {path} was not valid JSON; overwriting")
        return {}


def _toml_scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_scalar(x) for x in v) + "]"
    return json.dumps(str(v))  # JSON string == TOML basic string (quoting/escapes)


def _toml_tables(section: str, servers: dict) -> str:
    """Render ``[section.<name>]`` tables for an mcp mapping (codex).

    A nested dict (e.g. ``env``) becomes a sub-table ``[section.<name>.env]``.
    Scalars and lists of scalars render inline. Deliberately small -- MCP server
    entries are shallow.
    """
    out: list[str] = []
    for name, conf in servers.items():
        conf = conf if isinstance(conf, dict) else {}
        subtables = {k: v for k, v in conf.items() if isinstance(v, dict)}
        scalars = {k: v for k, v in conf.items() if not isinstance(v, dict)}
        out.append(f"[{section}.{name}]")
        for k, v in scalars.items():
            out.append(f"{k} = {_toml_scalar(v)}")
        for k, sub in subtables.items():
            out.append(f"[{section}.{name}.{k}]")
            for sk, sv in sub.items():
                out.append(f"{sk} = {_toml_scalar(sv)}")
        out.append("")
    return "\n".join(out)


def _apply_mcp(agent, workdir: Path, spec, warnings: list) -> None:
    kind, rel, key = spec
    target = workdir / rel
    if kind == "json":
        data = _load_json(target, warnings, f"agent {agent.name}: mcp")
        servers = data.get(key)
        if not isinstance(servers, dict):
            servers = {}
        servers.update(agent.mcp)
        data[key] = servers
        _write_text(target, json.dumps(data, indent=2) + "\n")
    else:  # toml (codex) -- APPEND server tables to the existing config.toml
        block = _toml_tables(key, agent.mcp)
        base = target.read_text() if target.is_file() else ""
        # Avoid duplicating tables on a re-run: drop any prior [key.*] block we own.
        kept = _strip_toml_section(base, key)
        merged = (kept.rstrip() + "\n\n" if kept.strip() else "") + block
        try:
            import hooks

            if not hooks.valid_toml(merged):
                warnings.append(
                    f"agent {agent.name}: mcp produced invalid {rel}; skipped"
                )
                return
        except ImportError:  # pragma: no cover - hooks is always present
            pass
        _write_text(target, merged)


def _strip_toml_section(text: str, section: str) -> str:
    """Remove every ``[section.*]`` table (and its body) from *text*.

    Keeps everything else verbatim so codex's ``notify`` / trust lines survive a
    re-materialise. A table runs until the next top-level ``[`` header or EOF.
    """
    lines = text.splitlines()
    out: list[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            header = stripped.strip("[]")
            skip = header == section or header.startswith(section + ".")
        if not skip:
            out.append(line)
    return "\n".join(out)


def _apply_settings(agent, workdir: Path, spec, warnings: list) -> None:
    _kind, rel, _key = spec
    target = workdir / rel
    data = _load_json(target, warnings, f"agent {agent.name}: settings")
    data.update(agent.settings)  # shallow merge; preserves hooks already present
    _write_text(target, json.dumps(data, indent=2) + "\n")


def _apply_skills(agent, workdir: Path, skills_rel: str, warnings: list) -> None:
    dest_root = workdir / skills_rel
    for src in agent.skills:
        src = Path(src)
        dest = dest_root / src.name
        if dest.exists():
            shutil.rmtree(dest)
        try:
            shutil.copytree(src, dest)
        except OSError as exc:  # pragma: no cover - defensive
            warnings.append(f"agent {agent.name}: could not copy skill {src}: {exc}")


def apply(cfg, agent) -> list[str]:
    """Materialise *agent*'s config into its workdir. Returns warnings.

    Called from ``cli.start_agent`` after the workdir exists and turn-detection
    (which writes ``.claude/settings.json``) is installed, so settings merge
    rather than clobber. No-op for an agent that declares none of the keys.
    """
    warnings: list[str] = []
    workdir = Path(agent.workdir)
    profile = PROFILE.get(agent.type, _DEFAULT_PROFILE)

    # context -> the type's context filename.
    if agent.context:
        _write_text(workdir / profile["context"], agent.context.rstrip("\n") + "\n")

    # files -> generic escape hatch (relative paths validated at config load).
    for rel, content in agent.files.items():
        _write_text(workdir / rel, content)

    # mcp
    if agent.mcp:
        if profile["mcp"] is None:
            warnings.append(
                f"agent {agent.name}: `mcp` has no known config location for type "
                f"{agent.type!r}; ignored (use `files:` to place one manually)"
            )
        else:
            _apply_mcp(agent, workdir, profile["mcp"], warnings)

    # settings
    if agent.settings:
        if profile["settings"] is None:
            warnings.append(
                f"agent {agent.name}: `settings` is not supported for type "
                f"{agent.type!r}; ignored (use `files:` instead)"
            )
        else:
            _apply_settings(agent, workdir, profile["settings"], warnings)

    # skills (Claude-only feature)
    if agent.skills:
        if profile["skills"] is None:
            warnings.append(
                f"agent {agent.name}: `skills` are only supported for claude agents "
                f"(type {agent.type!r}); ignored"
            )
        else:
            _apply_skills(agent, workdir, profile["skills"], warnings)

    return warnings
