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

The source and interpreter are not present yet: downloading source in the setup
session failed because GitHub and python.org could not be resolved. See
[the environment report](docs/SETUP.md) for verified tools and missing packages.

## Start here

From `/home/sammli/python-linq`:

```bash
bash scripts/check-environment.sh
bash scripts/setup.sh
bash scripts/test-baseline.sh
```

Setup obtains the pinned source if absent, creates a development branch for a
new clone, and builds a debug interpreter. It never invokes sudo or installs over
system Python. Run the downloaded interpreter as `./build/debug/python`.

If this session still has no network access, obtain the source from a normal
terminal using the commands in [SETUP.md](docs/SETUP.md), then retry setup.

Compiler changes belong in `cpython/`, which retains its upstream Git history.
The outer workspace ignores that checkout and generated build/log files. No
outer Git repository has been initialized by the setup work.

Read [the project plan](docs/PLAN.md) before starting compiler changes.
