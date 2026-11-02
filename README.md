# Python LINQ

An experimental native CPython extension for declarative queries over lists.
The current phase is environment setup; no LINQ implementation has started.

## Workspace

```text
python-linq/
  cpython/       # CPython v3.14.0 source; separate Git repository
  build/debug/   # Out-of-tree debug interpreter
  docs/          # Project plan and environment notes
  scripts/       # Environment, setup, and baseline-test commands
  logs/          # Configure, build, and test output
```

Setup supports macOS and Linux, including Ubuntu under WSL. See
[the setup guide](docs/SETUP.md) for platform dependencies and troubleshooting.

## Start here

From the repository root, after installing the dependencies in the setup guide:

```bash
bash scripts/check-environment.sh
bash scripts/setup.sh
bash scripts/test-baseline.sh
```

Setup obtains the pinned source if absent, creates a development branch for a
new clone, and builds a debug interpreter. It never invokes sudo or installs over
system Python. Run the built interpreter as `./build/debug/python` (or
`./build/debug/python.exe` on case-insensitive filesystems, common on macOS).
The scripts detect either name automatically.

If this session still has no network access, obtain the source from a normal
terminal using the commands in [SETUP.md](docs/SETUP.md), then retry setup.

Compiler changes belong in `cpython/`, which retains its upstream Git history.
The outer workspace ignores that checkout and generated build/log files. The
outer Git repository tracks project scripts and documentation.

Read [the project plan](docs/PLAN.md) before starting compiler changes.
