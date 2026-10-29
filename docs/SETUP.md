# Environment setup

## Observed on 2026-10-29

- Workspace: `/home/sammli/python-linq`; file access works in the current session.
- OS: Ubuntu 22.04.3 LTS, Linux.
- GCC: 11.4.0.
- GNU Make: 4.3.
- Git: 2.34.1.
- System Python: `/usr/bin/python3`, version 3.10.12.
- The default `python3` command points into Miniconda. The project interpreter
  will instead be explicitly invoked as `build/debug/python`.
- Disk space: approximately 494 GiB available when inspected.
- GitHub clone failed with `Could not resolve host: github.com`.
- Official source-archive download failed with `Could not resolve host: www.python.org`.
- No source checkout, compiler build, or compiler baseline tests have completed.

## Recommended dependencies

Installed packages checked: `build-essential`, `pkg-config`, `libssl-dev`,
`zlib1g-dev`, `libreadline-dev`, `libncurses-dev`, and `uuid-dev`.

These recommended development packages were missing. They enable additional
standard-library modules; their absence does not necessarily prevent a minimal
interpreter build. Install them from a normal terminal for a fuller development
environment:

```bash
sudo apt-get update
sudo apt-get install libbz2-dev libsqlite3-dev libffi-dev liblzma-dev \
  libgdbm-dev libgdbm-compat-dev tk-dev libzstd-dev
```

Project file creation, source checkout, and local builds do not require sudo.
System package installation may require it. The setup scripts do not run sudo.

## Acquire source outside this session if necessary

From a terminal with network access:

```bash
cd /home/sammli/python-linq
git clone --depth 1 --branch v3.14.0 --single-branch \
  https://github.com/python/cpython.git cpython
git -C cpython switch -c python-linq
```

This preserves upstream source history in the nested `cpython` repository.
Do not run these clone commands over an existing source directory.

If GitHub is unavailable but python.org works, an official archive is another
option (it does not include Git history):

```bash
cd /home/sammli/python-linq
curl --fail --location --output Python-3.14.0.tar.xz \
  https://www.python.org/ftp/python/3.14.0/Python-3.14.0.tar.xz
tar -xf Python-3.14.0.tar.xz
mv Python-3.14.0 cpython
```

## Build and verify

```bash
cd /home/sammli/python-linq
bash scripts/check-environment.sh
bash scripts/setup.sh
bash scripts/test-baseline.sh
```

Setup uses four build jobs by default. Override with `BUILD_JOBS=8 bash
scripts/setup.sh` if desired. Logs are written under `logs/`; inspect configure
and build output for missing or failed extension modules. Record baseline skips
and failures before making compiler changes.

No `make install` is needed. The out-of-tree debug build leaves the source tree
available for compiler work without replacing system Python.

After grammar or AST changes, use the matching regeneration targets from the
configured build directory, then rebuild and run focused tests:

```bash
cd /home/sammli/python-linq/build/debug
make regen-ast regen-pegen regen-keyword
make -j4
```

## References

- CPython setup: https://devguide.python.org/getting-started/setup-building/
- Pinned source: https://github.com/python/cpython/tree/v3.14.0
- Compiler overview: https://github.com/python/cpython/blob/v3.14.0/InternalDocs/compiler.md
- Grammar changes: https://github.com/python/cpython/blob/v3.14.0/InternalDocs/changing_grammar.md
