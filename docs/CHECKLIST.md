# Native LINQ implementation checklist

Specification: [PLAN.md](PLAN.md). Build commands: [SETUP.md](SETUP.md).

The feature is not implemented. Checkboxes below represent completed tasks;
separate `M*.V` gates require recorded verification evidence. A milestone is not
verified merely because implementation tasks are checked. Keep task IDs stable.

## Milestone status and dependencies

| Milestone | Depends on | Implementation status | Verification status |
| --- | --- | --- | --- |
| M1: patch delivery | None | Not started | Pending |
| M2: frontend and C runtime | M1 | Not started | Pending |
| M3: unoptimized IR and lowering | M2 | Not started | Pending |
| M4: CFG and data-flow analysis | M3 | Not started | Pending |
| M5: bounded fusion | M4 | Not started | Pending |
| M6: integration and explanation | M1-M5 | Not started | Pending |

## M1: reproducible CPython patch delivery

- [ ] **M1.1** Define the ordered tracked patch series against v3.14.0, including
  generated outputs and base/checkpoint commit records.
- [ ] **M1.2** Integrate patch replay with setup: identify already-applied patches,
  apply missing patches only to the expected clean base/prefix, and preserve work.
- [ ] **M1.3** Diagnose incompatible/dirty states clearly; allow local development
  when required patches are already present. Document archive-source limitations.
- [ ] **M1.4** Document export, replay, and recovery procedures for the outer
  repository and ignored nested development checkout.
- [ ] **M1.V** Verify clean reconstruction, repeated setup, applied-prefix handling,
  and safe refusal on incompatible/local modifications; record commands/results.

## M2: frontend, binding, and independent C runtime

- [ ] **M2.1** Record token examples; integrate contextual keyword metadata without
  new token kinds; verify existing `where`/`select` identifiers and imports.
- [ ] **M2.2** Implement parenthesized query grammar, nesting, ordered clauses,
  and focused invalid-syntax diagnostics.
- [ ] **M2.3** Add `QueryExpr(target, source, predicates, selector)`, locations,
  AST validation, and explicit rejection of excluded targets/expressions.
- [ ] **M2.4** Traverse queries in preprocessing while preserving public AST
  structure for ordinary/optimized AST requests.
- [ ] **M2.5** Implement source binding in the surrounding scope and implicit
  predicate/selector function scopes using stable original-expression keys.
- [ ] **M2.6** Update Python/C unparsing, AST traversal/round trips, compiler
  tooling tests, reference documentation, and generated files.
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
- [ ] **M3.V** Demonstrate complete unoptimized queries, explain their IR and
  disassembly, and pass focused semantic and existing compiler regressions.

**Checkpoint:** native filtering/projection is complete here; no fusion claims yet.

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
  reflection, and unsupported control flow. No automatic rewrite is required yet.

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
- [ ] **M5.6** Add structural inspection and test-only counters establishing an
  actual bypass, beyond matching outputs or runtime composition alone.
- [ ] **M5.V** Verify equal values and callback event logs with fusion on/off,
  including source rebinding/mutation, captures, short circuit, partial iteration,
  exceptions, repeated iteration, runtime guards, and retained producer timing.

**Checkpoint:** analysis changes generated execution. Both paths remain lazy and
O(n); producer allocation is retained. Do not claim materialization elimination.

## M6: integration, portability, and author walkthrough

- [ ] **M6.1** Complete dedicated syntax, AST/tooling, semantic, timing, binding,
  runtime ownership, analysis, and optimization tests from the specification.
- [ ] **M6.2** Run debug-build reference-leak checks for query/iterator cycles,
  failures, exhaustion, and partial consumption.
- [ ] **M6.3** Pass the six baseline suites and run the broader CPython suite;
  distinguish new failures from baseline/environment skips and failures.
- [ ] **M6.4** Add automated real macOS and Ubuntu/Linux build/test coverage;
  record manual Ubuntu-on-WSL setup, build, and feature-test results.
- [ ] **M6.5** Benchmark fused/unfused pipelines reproducibly; report measured
  time and iterator/callback overhead rather than assuming improved complexity.
- [ ] **M6.6** Replay the final patch series onto a clean pinned checkout and
  demonstrate the feature and opt-in optimizer from the documented setup.
- [ ] **M6.7** Explain each changed stage: tokens, grammar, AST, binding, query IR,
  analysis proof, fusion, closure/runtime lowering, bytecode, and C execution.
- [ ] **M6.V** Record final evidence, relevant commits/patches, platform limitations,
  unsupported cases, and a demonstration of both normal and optimized execution.

## Evidence and decision records

Append a record when completing each verification gate:

```text
Milestone/task IDs:
Implementation commit(s) and patch-series checkpoint:
Platform, architecture, compiler, and CPython build:
Changed areas and rationale:
Demonstration / walkthrough:
Commands and results (including skips/failures):
IR / definition-use / disassembly evidence, where applicable:
Unresolved limitations:
```

Keep implementation and verification status in the milestone table current. Record
scope changes here and in PLAN.md before implementing them; do not silently expand
SSA, backend, query syntax, or optimization guarantees.

After M6, ordering is the first extension candidate. Broader operators, iterable
sources, projection/loop fusion, producer-allocation elimination, new opcodes,
and SSA require separate planning; they are not incomplete v1 checklist tasks.
