# Python LINQ

A CPython 3.14.0 compiler project adding native declarative queries over lists.
The initial grammar and `QueryExpr` AST are present. Query execution, binding,
query IR, and optimization remain to be implemented.

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
bazel run //:python -- -VV
```

The smoke test compares every override with the generated source archive and
uses the extracted distribution's debug interpreter to parse query syntax into
`ast.QueryExpr`.
It is a build integration check; query execution is not implemented yet.

The distribution is `bazel-bin/python-linq.tar.gz`. Exact commands, artifact
paths, and verification results are in [SETUP.md](docs/SETUP.md).

Implementation scope: [PLAN.md](docs/PLAN.md).
Progress: [CHECKLIST.md](docs/CHECKLIST.md).
