"""Helpers for exercising the lib modules without a real tmux or API keys.

``mock_tmux`` patches ``tmux.subprocess.run`` (which ``tmux.tmux()`` calls) and
``shutil.which`` so every tmux command "succeeds". The routing / mail / ACL
logic is exercised directly through the lib functions, so the paste layer is
only under test where a test opts in.
"""

import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import config as cfgmod


class TmuxRunner:
    """Stand-in for ``subprocess.run`` that answers the calls ``tmux.tmux()`` makes."""

    def __init__(self, has_session=True, pane="", returncode=0):
        self.has_session = has_session
        self.pane = pane
        self.returncode = returncode
        self.calls = []

    def __call__(self, args, *a, **kw):
        import tmux as tmod  # local import so support works before tmux.py lands

        cmd = list(args)
        self.calls.append(cmd)
        if "has-session" in cmd:
            if self.has_session:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise subprocess.CalledProcessError(1, cmd, "")
        if cmd and len(cmd) > 2 and cmd[1] == "capture-pane":
            return subprocess.CompletedProcess(cmd, self.returncode, self.pane, "")
        return subprocess.CompletedProcess(cmd, self.returncode, "", "")


@contextmanager
def mock_tmux(has_session=True, pane="", returncode=0):
    import tmux as tmod

    runner = TmuxRunner(has_session=has_session, pane=pane, returncode=returncode)
    with mock.patch.object(tmod.subprocess, "run", runner), mock.patch.object(
        tmod.shutil, "which", lambda name: "/usr/bin/" + name
    ):
        yield runner


def write_config(tmp_path, body, name="agentainer.yaml"):
    path = tmp_path / name
    path.write_text(body)
    return path


def agent_yaml(tmp_path, agents_block, **swarm_over):
    """Build a minimal valid agentainer config with the given agents + swarm overrides.

    Uses the v2 ``role:`` field (v1's ``first_prompt`` was renamed).
    """
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    swarm_bits = [f"root: {root}", 'session_prefix: "t-"']
    for k, v in swarm_over.items():
        swarm_bits.append(f"{k}: {v}")
    return write_config(
        tmp_path,
        "swarm:\n  " + "\n  ".join(swarm_bits) + "\n"
        "defaults: {type: claude}\n"
        "agents:\n" + agents_block,
    )


def load_swarm(tmp_path, agents_block, **swarm_over):
    """Write an agent_yaml and return the loaded SwarmConfig."""
    path = agent_yaml(tmp_path, agents_block, **swarm_over)
    return cfgmod.load(path)
