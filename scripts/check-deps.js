#!/usr/bin/env node
// Agentainer dependency doctor (npm postinstall + `agentainer doctor`).
//
// Verifies the two runtime dependencies that are not Python: a Python 3
// interpreter and tmux. PyYAML is intentionally NOT required -- Agentainer
// ships a bundled fallback parser so it runs on a bare stdlib Python.
"use strict";

const { spawnSync } = require("child_process");

function present(cmd) {
  const r = spawnSync(cmd, ["--version"], { stdio: "ignore" });
  return !r.error && r.status === 0;
}

const python = present("python3") || present("python");
const tmux = present("tmux");

if (!python) {
  process.stderr.write(
    "xx Agentainer needs python3 on PATH (or set AGENTAINER_PYTHON).\n"
  );
  process.exit(1);
}

if (!tmux) {
  process.stderr.write(
    "!! tmux was not found on PATH; every command except 'validate' will fail.\n" +
      "   Install tmux (apt-get install tmux / brew install tmux) before `up`.\n"
  );
}

process.exit(0);
