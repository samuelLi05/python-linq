# Native LINQ demonstration

Run these commands from the repository root with the built CPython 3.14.0
interpreter. Setup/toolchain requirements are in [SETUP.md](../docs/SETUP.md).

## Prepare the interpreter

Before the demonstration, build and verify the local and packaged runtimes:

```bash
./build.sh
./build/python tests/test_queries.py -v
bazel test //:build_smoke --test_output=all
./build/python -m test --timeout 120 \
  test_symtable test_ast test_unparse test_grammar \
  test_tokenize test_keyword test_compile test_dis
```

The demonstration itself uses the exported `build/python` and needs no rebuild.
Compiler edits still require `./build.sh`. Prepare downloaded dependencies and
measure that rebuild on the machine used for the demonstration.

## Filtering, projection, and deferred execution

Read [basic.py](basic.py), then run:

```bash
./build/python demos/basic.py
```

Expected output:

```text
before: []
first: 160
events: [(50, False), (80, True)]
remaining: [180, 160]
```

Construction does not call the predicate. The first request examines only `50`
and `80`, then projects `80` to `160`. Later requests preserve the remaining
order and duplicate `80`. `minimum` and `scale` are captured function parameters;
`score` is the implicit clause parameter.

## Analysis, IR, and generated calls

[fusion.py](fusion.py) contains consecutive identity-projection query assignments.
Inspect them without executing the file:

```bash
./build/python scripts/linq.py analyze --summary demos/fusion.py
./build/python scripts/linq.py ir --fusion --summary demos/fusion.py
./build/python scripts/linq.py dis --fusion --summary demos/fusion.py
```

The source of `q0` is a parameter rather than a local query definition, so it is
ineligible as a merge consumer. `q1` is eligible to merge with `q0`. This concerns
optimization eligibility, not whether either query is valid.

The fused IR summary includes:

```text
rewrite q1: normal -> fused producer=q0
```

The disassembly summary reports actual imports `['_query', '_fused_query']` in
`make`, plus the generated clause parameter/free-variable information. Use
`--no-fusion` to see two `_query` calls. Omit `--summary` for full JSON reports or
full disassembly; `--optimize` remains independent of fusion.

## Equal results and less projection work

```bash
./build/python scripts/linq.py run --no-fusion demos/fusion.py --counts
./build/python scripts/linq.py run --fusion demos/fusion.py --counts
```

Both print `[2, 4]`. The optional `--counts` argument uses private counters from
the project's debug build and disables counting in cleanup.

| Observation | Normal | Fused |
| --- | --- | --- |
| Query objects | 2 | 2 |
| Projection stages | 2 | 1 |
| Projection calls | 6 | 2 |

The producer is still constructed. Fusion bypasses its identity projection and
iterator-chain construction while preserving filter order and the consumer
selector. Both pipelines are already lazy; these counts do not demonstrate an
asymptotic improvement or measure elapsed-time performance.

Without `--counts`, the program prints only the result list.

## Valid programs outside the fusion proof

Read [analysis.py](analysis.py), then inspect and execute it:

```bash
./build/python scripts/linq.py analyze --summary demos/analysis.py
./build/python scripts/linq.py run --fusion demos/analysis.py
```

The `ambiguous` function has two reaching producer definitions at the join. Its
consumer reports `ineligible`, with reason `ambiguous_producer` and both
definition IDs. The `unsupported` function contains a loop, so its candidates
report `unknown` under the whole-function analysis policy. Neither function is
fused, even though both execute correctly:

```text
positive branch: [2, 4]
negative branch: [-4, -2]
unsupported flow: [2, 4]
```

An unknown result means the bounded analysis did not establish the required
proof. It does not mean the language rejects the program.

## Scope

These examples demonstrate native `from`/`where`/`select`, implicit clause scopes,
lazy native iteration, CFG/data-flow evidence, and guarded opt-in fusion.
Sorting, joins, grouping, sets, aggregation, and other operator families remain
[deferred](../docs/PLAN.md#deferred-extensions).

The demo gate verifies the examples and existing tests on exported and packaged
interpreters. Broader release validation, platform coverage, reference-leak
audits, clean-environment reconstruction, and benchmarks remain in the
[validation backlog](../docs/CHECKLIST.md#deferred-validation).
