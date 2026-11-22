# Python LINQ

A CPython 3.14.0 compiler project adding native declarative queries over lists.
Query parsing, AST tooling, binding, compiler-owned query IR, native lowering,
a lazy C runtime, control/data-flow analysis, and guarded opt-in filter fusion
are implemented. The runnable [demo guide](demos/README.md) covers the base
language, analysis limits, and opt-in fusion. Broader release validation remains
in the [backlog](docs/CHECKLIST.md#deferred-validation).

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
distribution's interpreter. Native query syntax executes through compiled
predicate/selector functions and the private C runtime factory.

The captured interpreter is `build/python`, with its executable and libraries
under `build/python-linq/`. The captured archive is `build/python-linq.tar.gz`.
These outputs are ignored by Git. Build details are in [SETUP.md](docs/SETUP.md).

Language and runtime contracts: [LANGUAGE.md](docs/LANGUAGE.md).
Implementation scope: [PLAN.md](docs/PLAN.md).
Progress: [CHECKLIST.md](docs/CHECKLIST.md).

## Inspect and execute queries

Use the built interpreter with a source file containing query syntax:

```bash
./build/python scripts/linq.py ir path/to/program.py
./build/python scripts/linq.py analyze path/to/program.py
./build/python scripts/linq.py dis path/to/program.py
./build/python scripts/linq.py run path/to/program.py argument
```

Add `--summary` to `ir`, `analyze`, or `dis` for compact views.
`ir` prints compiler plans and analysis; `analyze` prints CFG/data-flow evidence
and fusion eligibility. `dis` shows bytecode and clause code objects. These
inspection commands do not execute the submitted program. All subcommands accept
`--optimize {-1,0,1,2}` before the filename; `-1` uses the interpreter setting.

Analysis reports eligible, ineligible, or unknown candidates. Unsupported
constructs make the whole function unknown while its queries continue executing
normally. Fusion is disabled by default and independent of `-O`. Enable it for
an explicit source compilation through the driver:

```bash
./build/python scripts/linq.py ir --fusion path/to/program.py
./build/python scripts/linq.py dis --fusion path/to/program.py
./build/python scripts/linq.py run --fusion path/to/program.py argument
```

Use `--no-fusion` for the normal path. IR output includes before/after rewrite
snapshots. Direct callers can use `compile(..., flags=ast.PyCF_LINQ_FUSION)`.
The flag does not implicitly enable fusion in imports or later dynamic compilation.

## Run the examples

```bash
./build/python demos/basic.py
./build/python scripts/linq.py analyze --summary demos/analysis.py
./build/python scripts/linq.py ir --fusion --summary demos/fusion.py
./build/python scripts/linq.py run --fusion demos/fusion.py --counts
```

The [demo guide](demos/README.md) includes expected output, normal/fused
comparisons, and preflight commands. It uses the existing compiler and runtime.
