# Build and source overrides

The build targets macOS, native Linux, and Linux under WSL. Run the commands
below from the repository root. Native Windows is outside the current scope.

## Prerequisites

Use Bazel **9.0.1**, as pinned in `.bazelversion`. Bazelisk selects that version
automatically. The first build downloads the upstream source, declared native
libraries, build rules, and a managed Python 3.12 interpreter for regeneration.

On macOS, install Apple's Command Line Tools and Bazelisk:

```bash
xcode-select --install
brew install bazelisk
bazel version
```

On Debian/Ubuntu or Ubuntu under WSL, install the native build tools:

```bash
sudo apt-get update
sudo apt-get install build-essential bash curl unzip zip patch perl
```

Install Bazelisk using its [official installation instructions](https://github.com/bazelbuild/bazelisk#installation),
or install Bazel 9.0.1 directly. WSL uses Linux tools inside the distribution.
No separately installed Python or Homebrew development libraries are required
for the build. The host C/C++ compiler, SDK/system headers, shell, and Make are
still prerequisites; Bazel sandboxing does not replace those platform tools.

## Tracked files and build inputs

| Path | Role |
| --- | --- |
| `MODULE.bazel`, `MODULE.bazel.lock` | Pinned upstream archive, native libraries, rules, and managed host Python. |
| `BUILD.bazel` | Regeneration, foreign build, installed runtime, distribution, and smoke-test targets. |
| `bazel/cpython.bzl` | Declared source-overlay, static-library, packaging, and interpreter-launcher rules. |
| `patches/<CPython-relative-path>` | Complete replacement source files to commit in this repository. |
| `cpython/` | Optional ignored reference checkout; never a build input or edit destination. |
| `bazel-bin/` | Convenience symlink to Bazel's generated artifacts. |

Bazel also creates `bazel-out`, `bazel-testlogs`, and `bazel-python-linq`
symlinks into its cache. `/bazel-*` in `.gitignore` excludes all of these.
Their dependency sources and intermediate outputs do not belong in Git.

For example, edit `patches/Grammar/python.gram` to change the grammar. A new
override must contain the entire replacement file at its CPython-relative path.
`glob(["patches/**"])` declares these files as action inputs, so changing one
invalidates the source action and the dependent CPython build.

Bazel verifies the upstream archive checksum; the source action produces
`source.tar` with overrides replacing matching upstream paths. `rules_foreign_cc`
unpacks it into its build scratch area. It then runs configure followed by
`regen-token`, `regen-ast`, `regen-pegen`, `regen-keyword`, `all`, and `altinstall`.
Generated parser/AST files belong to the build outputs; author their inputs in
`patches/`. No patch replay, nested Git commits, or edits to upstream are needed.

## Build

```bash
./build.sh
```

This builds `//:dist` and produces `bazel-bin/python-linq.tar.gz`.
`bash scripts/setup.sh` delegates to the same launcher. To choose the CPython
Make parallelism:

```bash
BUILD_JOBS=8 ./build.sh
```

The default is four CPython Make jobs. Bazel schedules native dependency
compilation separately; its `--jobs` option controls that scheduling.

Build individual outputs when needed:

```bash
bazel build //:source_archive
bazel build //:runtime //:python
bazel build //:build_logs
```

| Target | Output |
| --- | --- |
| `//:source_archive` | `bazel-bin/cpython_inputs/source.tar` |
| `//:runtime` | `bazel-bin/copy_python-linq/python-linq/` |
| `//:python` | Launcher used by `bazel run //:python -- ...` |
| `//:dist` | `bazel-bin/python-linq.tar.gz` |
| `//:build_logs` | Foreign build script/log artifacts; Bazel prints their paths. |

The interpreter is a debug build (`--with-pydebug`), installed into a Bazel
artifact with its standard library. No system Python installation is changed.
The macOS toolchain targets macOS 11.0 or newer for both build tools and runtime.
OpenSSL, compression libraries, SQLite, libffi, readline, and ncurses come from
the declared dependency graph. GDBM, DBM, Tk, and ensurepip are disabled in this
build configuration.

## Build verification

Only the source-override integration and built-interpreter checks are in scope
for the current build smoke test:

```bash
bazel test //:build_smoke --test_output=all
bazel run //:python -- -VV
```

`build_smoke` compares every override byte-for-byte with its entry in
the source artifact. It rejects links in the distribution archive, extracts it
into a temporary directory, and launches its `bin/python-linq` interpreter.
It checks Python
3.14.0 and `Py_DEBUG=1`, verifies `where`/`select` soft-keyword metadata, and
parses `(from x in [1, 2, 3] where x > 1 select x * 2)` into `ast.QueryExpr`.
This verifies that the overridden grammar and AST reach the actual interpreter.
Compiling that AST must raise the explicit unimplemented-execution error,
exercising AST validation, preprocessing, and the current code-generation guard.

To inspect that AST directly:

```bash
bazel run //:python -- -c 'import ast; print(ast.dump(ast.parse("(from x in [1, 2, 3] where x > 1 select x * 2)"), indent=2))'
```

Query compilation currently raises `SyntaxError: LINQ query execution is not
implemented yet`. Parsing support does not complete M2 binding/runtime or M3
execution. No broader compiler regression suites are part of this smoke check.

To use the packaged artifact outside Bazel, choose an extraction directory:

```bash
mkdir -p build/dist
tar -xzf bazel-bin/python-linq.tar.gz -C build/dist
./build/dist/python-linq/bin/python-linq -VV
```

The packaged launcher sets `PYTHONHOME` relative to its installation. Build
artifacts target the platform on which they were built; the archive is not a
single cross-platform binary.

The older `scripts/check-environment.sh` and `scripts/test-baseline.sh` describe
the former manual `build/debug` workflow and are not used by these Bazel targets.

## Verification record

Verified on **2026-11-02**, macOS **26.6.2**, **arm64**, Apple Clang **17.0.0**,
using Bazel **9.0.1**:

| Command | Result |
| --- | --- |
| `bazel build //:dist //:python //:source_archive` | Passed: CPython regenerated, compiled, installed, and packaged. |
| `./build.sh` | Passed: default distribution target produced `bazel-bin/python-linq.tar.gz` (94 MB). |
| `bazel test //:build_smoke --test_output=all` | Passed: all 5 overrides match; extracted distribution runs Python 3.14.0 with `Py_DEBUG=1`, produces `QueryExpr`, and rejects unimplemented execution. |
| `bazel run //:python -- -VV` | Passed: `Python 3.14.0 (main, redacted, redacted) [Clang 17.0.0 (clang-1700.6.4.2)]`. |
| `git -C cpython status --short` | Clean: reference checkout unchanged. |

Smoke-test output is available at `bazel-testlogs/build_smoke/test.log`.
Build timestamps in the interpreter version are deliberately redacted.
Linux and WSL require separate runs on those platforms. Query execution,
complete binding/tooling behavior, and broader compiler regression testing
remain outside this build-verification result.
