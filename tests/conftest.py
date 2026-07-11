"""Shared pytest fixtures and stdlib-only helpers for the Agentainer test suite.

The suite targets 100 % line coverage of ``lib/``. tmux is mocked everywhere
except where an integration test opts in, so the suite runs fast and offline.
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import config as cfgmod  # noqa: E402
import minyaml  # noqa: E402

EXAMPLE_CONFIGS = sorted((REPO / "examples").glob("*.yaml")) + [REPO / "quickstart.yaml"]


@pytest.fixture
def tmp_runtime(tmp_path):
    """A SwarmConfig whose runtime dirs live under a temp path (no real tmux)."""
    root = tmp_path / "ws"
    root.mkdir()
    cfg = cfgmod.SwarmConfig(
        path=tmp_path / "agentainer.yaml",
        name="t",
        root=root,
        session_prefix="t-",
        agents=[],
    )
    return cfg


def load_config(text, tmp_path):
    """Write *text* to a temp YAML, resolve its root, and return the loaded config."""
    path = tmp_path / "agentainer.yaml"
    path.write_text(text)
    return cfgmod.load(path)
