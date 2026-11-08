# Python LINQ

A CPython 3.14.0 compiler project adding native declarative queries over lists.
Query parsing, AST tooling, binding, and a native lazy runtime are implemented.
Query-syntax lowering, query IR, and optimization remain to be implemented.

Compiler changes are tracked as complete replacement files under `patches/`,
using CPython-relative paths. The ignored `cpython/` checkout is reference source;
the build does not read or modify it.

Bazel combines a checksum-pinned upstream archive with those overrides, then
uses `rules_foreign_cc` to regenerate and build CPython in its sandbox. The
installed interpreter and distribution archive are declared build artifacts.

## Build and verify

Install Bazel 9.0.1 (or Bazelisk) and the native toolchain described in
[SETUP.md](docs/SETUP.md), then run from the repository root:

```bash
./build.sh
bazel test //:build_smoke --test_output=all
./build/python tests/test_queries.py -v
```

The smoke test compares every override with the generated source archive and
runs the tracked frontend and runtime behavior tests against the extracted
distribution's interpreter. Native runtime queries execute through the private
factory; query syntax still rejects execution until lowering is implemented.

The captured interpreter is `build/python`, with its executable and libraries
under `build/python-linq/`. The captured archive is `build/python-linq.tar.gz`.
These outputs are ignored by Git. Build details are in [SETUP.md](docs/SETUP.md).

Language and runtime contracts: [LANGUAGE.md](docs/LANGUAGE.md).
Implementation scope: [PLAN.md](docs/PLAN.md).
Progress: [CHECKLIST.md](docs/CHECKLIST.md).
