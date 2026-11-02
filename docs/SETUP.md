# Environment setup

The same setup and baseline-test scripts run on macOS, native Linux, and Linux
under WSL. WSL uses its Linux distribution's tools and dependencies. Run these
scripts in Bash, including when your interactive macOS shell is Zsh.

## macOS dependencies

Install Apple's Command Line Tools if they are not already available:

```bash
xcode-select --install
```

If you have Homebrew, install these recommended libraries and package discovery
tools for a fuller standard library:

```bash
brew install pkgconf openssl@3 readline sqlite xz zstd
```

The scripts use the selected C compiler (`CC`, or `cc` by default), which on
macOS is normally Apple Clang. Setup detects Homebrew's prefix, including Apple
Silicon and Intel installations, and adds installed libraries to `CPPFLAGS`,
`LDFLAGS`, and `PKG_CONFIG_PATH` while preserving existing values. Homebrew is
optional; missing libraries can result in missing standard-library modules.
Additional modules such as Tk and GDBM may need their own libraries.

## Linux and WSL dependencies

On Debian/Ubuntu, including Ubuntu under WSL:

```bash
sudo apt-get update
sudo apt-get install build-essential pkg-config libssl-dev zlib1g-dev libbz2-dev \
  libreadline-dev libsqlite3-dev libffi-dev liblzma-dev libncurses-dev \
  libgdbm-dev libgdbm-compat-dev uuid-dev tk-dev libzstd-dev
```

On other Linux distributions, use the distribution's package manager to install
a C compiler, Make, Git, Python 3, and development headers for OpenSSL, zlib,
bzip2, readline, SQLite, libffi, xz, ncurses, GDBM, UUID, Tk, and zstd. A
`pkg-config` implementation helps configure find these libraries. The build uses
the compiler and library search paths supplied by the Linux environment; the
Homebrew configuration is applied only on macOS.

Project file creation, source checkout, and local builds do not require sudo.
The setup scripts report missing dependencies but do not install packages or
invoke sudo. Run all commands below from this repository's root directory.

## Acquire source outside this session if necessary

From a terminal with network access:

```bash
git clone --depth 1 --branch v3.14.0 --single-branch \
  https://github.com/python/cpython.git cpython
git -C cpython switch -c python-linq
```

This preserves upstream source history in the nested `cpython` repository.
Do not run these clone commands over an existing source directory.

If GitHub is unavailable but python.org works, an official archive is another
option (it does not include Git history):

```bash
curl --fail --location --output Python-3.14.0.tar.xz \
  https://www.python.org/ftp/python/3.14.0/Python-3.14.0.tar.xz
tar -xf Python-3.14.0.tar.xz
mv Python-3.14.0 cpython
```

## Build and verify

```bash
bash scripts/check-environment.sh
bash scripts/setup.sh
bash scripts/test-baseline.sh
```

Setup uses four build jobs by default. Override with `BUILD_JOBS=8 bash
scripts/setup.sh` if desired. Logs are written under `logs/`; inspect configure
and build output for missing or failed extension modules. Record baseline skips
and failures before making compiler changes.

On case-insensitive filesystems (including most macOS installations), CPython
names the executable `python.exe`; elsewhere it is usually `python`. All three
scripts detect either name. For an interactive session use
`./build/debug/python.exe` or `./build/debug/python`, as appropriate.

No `make install` is needed. The out-of-tree debug build leaves the source tree
available for compiler work without replacing system Python.

After grammar or AST changes, use the matching regeneration targets from the
configured build directory, then rebuild and run focused tests:

```bash
cd build/debug
make regen-ast regen-pegen regen-keyword
make -j4
```

## References

- CPython setup: https://devguide.python.org/getting-started/setup-building/
- Pinned source: https://github.com/python/cpython/tree/v3.14.0
- Compiler overview: https://github.com/python/cpython/blob/v3.14.0/InternalDocs/compiler.md
- Grammar changes: https://github.com/python/cpython/blob/v3.14.0/InternalDocs/changing_grammar.md
