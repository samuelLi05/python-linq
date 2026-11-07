# Native LINQ implementation checklist

Specification: [PLAN.md](PLAN.md). Build commands: [SETUP.md](SETUP.md).

## Milestone dependencies

| Milestone | Depends on |
| --- | --- |
| M1: override delivery | None |
| M2: frontend and C runtime | M1 |
| M3: unoptimized IR and lowering | M2 |
| M4: CFG and data-flow analysis | M3 |
| M5: bounded fusion | M4 |
| M6: integration and validation | M1-M5 |

## M1: reproducible CPython override delivery

- [x] **M1.1** Pin the upstream v3.14.0 archive and checksum; track complete
  replacement files under `patches/` with CPython-relative paths.
- [x] **M1.2** Declare upstream and overrides as Bazel action inputs and produce
  a combined source artifact without modifying the reference checkout.
- [x] **M1.3** Integrate `rules_foreign_cc`, native dependencies, managed host
  Python, regeneration targets, debug build, and installed-runtime artifacts.
- [x] **M1.4** Provide `build.sh`, Bazel interpreter launcher, packaged
  distribution, and documented build/override commands.
- [x] **M1.5** Export the native interpreter and standard library into ignored
  `build/python-linq/`, provide the `build/python` launcher, and retain
  `build/python-linq.tar.gz`; refresh these outputs after successful builds.
- [x] **M1.V** Verify all overrides match the source artifact, functional checks
  pass against the exported and packaged interpreters, repeated export succeeds,
  and the local interpreter runs independently of Bazel runfiles.

## M2: frontend, binding, and independent C runtime

Architecture: [operator-extension boundaries](PLAN.md#modularity-requirements-for-the-basic-implementation).

- [x] **M2.1** Integrate contextual keyword metadata without
  new token kinds; verify existing `where`/`select` identifiers and imports.
- [x] **M2.2** Implement parenthesized query grammar, nesting, ordered clauses,
  and focused invalid-syntax diagnostics.
- [x] **M2.3** Add `QueryExpr(target, source, predicates, selector)`, locations,
  AST validation, and explicit rejection of excluded targets/expressions.
- [x] **M2.4** Traverse queries in preprocessing while preserving public AST
  structure for ordinary/optimized AST requests.
- [x] **M2.5** Implement source binding in the surrounding scope and implicit
  predicate/selector function scopes using stable original-expression keys.
- [x] **M2.6a** Implement Python/C unparsing, AST traversal/round trips, and
  compiler tooling tests.
- [ ] **M2.6b** Document query syntax and public AST interfaces.
- [ ] **M2.7** Implement the normal private C query factory, immutable native query
  type, per-interpreter state, and independent query iterators.
- [ ] **M2.8** Implement list/query source checks, deferred ordered execution,
  callback exception behavior, reference ownership, GC, and cleanup.
- [ ] **M2.V** Verify grammar/AST/binding tests and direct runtime-factory tests:
  results, timing, repeatability, errors, and lifetime behavior. Native execution
  through code generation is not required until M3.

## M3: unoptimized query IR and lowering

- [ ] **M3.1** Build compiler-owned C query plans after preprocessing and binding,
  before instruction generation; keep ordinary expressions as AST references.
- [ ] **M3.2** Represent source/query input, ordered filters, projection/identity
  metadata, source locations, bindings, and normal execution mode.
- [ ] **M3.3** Validate plans and provide deterministic inspection output without
  changing the public query AST or executing the inspected program.
- [ ] **M3.4** Compile clause functions through existing closure machinery, using
  the same scope keys established during binding.
- [ ] **M3.5** Lower plans to normal `_linq` C factory calls using existing
  instructions; evaluate sources once and avoid helper names in user globals.
- [ ] **M3.6** Execute native syntax in assignments, arguments, returns, and nested
  queries. Verify captures, target isolation, class contexts, and source binding.
- [ ] **M3.7** Add a developer source-execution/inspection driver backed by the
  shared C planning implementation; keep ordinary lowering unfused.
- [ ] **M3.V** Pass end-to-end native query tests, IR/code-generation checks,
  and existing compiler regression suites.

**Deliverable:** native filtering/projection without fusion.

## M4: control-flow and data-flow analysis

- [ ] **M4.1** Build the high-level function CFG for supported assignments,
  expressions, `if`/`else`, returns, and `pass`; conservatively mark unsupported
  functions/regions without blocking ordinary compilation.
- [ ] **M4.2** Compute GEN/KILL and reaching definitions to a fixed point; compute
  dominance and binding-aware definition/use information without SSA renaming.
- [ ] **M4.3** Check unique producer definitions, consumer source use, extra uses,
  reassignment, aliases, and closure capture/escape restrictions.
- [ ] **M4.4** Classify exact identity projections by binding; require a consumer
  filter and consecutive distinct-name assignments in one block for v1 rewrites.
- [ ] **M4.5** Expose eligible/ineligible/unknown diagnostics with source locations,
  reasons, and definition/use evidence through shared compiler/tooling analysis.
- [ ] **M4.V** Verify positive chains and negative/unknown cases: multiple reaching
  definitions, extra uses, transformations, aliases, captures, branches, loops,
  reflection, and unsupported control flow.

## M5: bounded opt-in fusion

- [ ] **M5.1** Add `ast.PyCF_LINQ_FUSION`: disabled by default, compilation-unit
  scope only, independent of `-O`, with explicit AST-only behavior.
- [ ] **M5.2** Transform only proven eligible IR inputs to fused execution mode;
  preserve producer construction/assignment, captured source, and callback objects.
- [ ] **M5.3** Implement the guarded fused C factory with exact native-type and
  identity-metadata checks, and normal-construction fallback.
- [ ] **M5.4** Select specialized factory calls only for authorized plans; append
  predicates in order and bypass the producer iterator/identity selector.
- [ ] **M5.5** Extend the developer driver for explicit flag-on/off source
  compilation, analysis explanations, and before/after IR; avoid cache ambiguity.
- [ ] **M5.6** Add IR/code-generation assertions and test-only counters for
  intermediate-iterator bypass and eliminated producer identity-selector calls.
- [ ] **M5.V** Verify equal values and callback event logs with fusion on/off,
  including source rebinding/mutation, captures, short circuit, partial iteration,
  exceptions, repeated iteration, runtime guards, and retained producer timing.

**Deliverable:** opt-in iterator fusion with retained producer allocation and
O(n) execution.

## M6: integration, portability, and validation

- [ ] **M6.1** Complete dedicated syntax, AST/tooling, semantic, timing, binding,
  runtime ownership, analysis, and optimization tests from the specification.
- [ ] **M6.2** Run debug-build reference-leak checks for query/iterator cycles,
  failures, exhaustion, and partial consumption.
- [ ] **M6.3** Pass the six baseline suites and run the broader CPython suite;
  distinguish new failures from baseline/environment skips and failures.
- [ ] **M6.4** Add automated real macOS and Ubuntu/Linux build/test coverage;
  record manual Ubuntu-on-WSL setup, build, and feature-test results.
- [ ] **M6.5** Measure runtime and iterator/callback overhead with reproducible
  fused/unfused benchmarks.
- [ ] **M6.6** Rebuild the final pinned archive and overrides in a clean Bazel
  environment and run native query and optimizer tests from its artifacts.
- [ ] **M6.V** Record final test results, commits/patches, platform limitations,
  and unsupported cases for normal and optimized execution.

## Deferred work

Specification: [deferred extensions](PLAN.md#deferred-extensions).

- [ ] Multiple sources and flattening.
- [ ] Query bindings and continuations.
- [ ] Sorting.
- [ ] Grouping and joins.
- [ ] Set operations.
- [ ] Quantifiers.
- [ ] Aggregation.
- [ ] Partitioning.
- [ ] Element selection.
- [ ] Concatenation and sequence comparison.
- [ ] Additional iterable sources.
- [ ] Projection/loop fusion and producer-allocation elimination.
