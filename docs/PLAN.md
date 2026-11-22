# Native LINQ for Python: implementation plan

Implementation tracking: [CHECKLIST.md](CHECKLIST.md).
Build instructions: [SETUP.md](SETUP.md).
Status: the frontend, AST tooling, binding, query IR, native syntax lowering,
lazy C runtime, control/data-flow analysis, and guarded opt-in fusion are
implemented. M6 delivers the runnable defense demonstration and code-explanation
material. Broader release validation remains a separate backlog.

## Goal, priorities, and boundaries

Extend CPython **v3.14.0** with native declarative queries over lists. This is a
compiler project involving grammar, ASTs, binding, a query intermediate
representation (IR), control-flow/data-flow analysis, lowering, and a C runtime.

Delivery order:

1. Native filtering/projection with correct deferred execution.
2. Query-merge analysis with explanations.
3. A bounded, opt-in fusion optimization that changes generated execution.
4. Additional operator families as independent extensions to the core pipeline.

The core checkpoints deliver native filtering/projection, analysis, and guarded
opt-in fusion. Their completion is not an architectural prerequisite for
every additional operator. Each extension depends on the core capabilities it
uses; the deferred inventory and extension requirements are specified below.
No fixed deadline is specified.

SSA is not required for the chosen optimization. We will use a function CFG,
reaching definitions, dominance, and definition/use information without renaming
Python variables or inserting phi nodes. CPython's existing bytecode backend is
reused. The main custom compiler work is semantic analysis and the middle end,
with a code-generation adapter and a separate C execution runtime.

Required platforms are macOS, native Linux, and Linux under WSL. Native Windows,
new VM instructions, a machine-code backend, JIT integration, static element-type
inference, and a general-purpose Python SSA IR are outside this scope.

## Language and execution contract

```python
scores = [97, 92, 81, 60]
passing = (
    from score in scores
    where score > 80
    select score
)
print(list(passing))  # [97, 92, 81]
```

The modified interpreter parses and executes this syntax through the native runtime.
The first version accepts:

```text
query := "(" "from" NAME "in" expression
             ("where" expression)*
             "select" expression ")"
```

- Require parentheses, one source, one identifier binding, zero or more ordered
  filters, and exactly one final projection.
- Allow queries in expression positions, including assignments, arguments,
  returns, and nested queries.
- Keep `from` and `in` as existing keywords. Treat `where` and `select` as
  contextual soft keywords so existing identifiers continue to work elsewhere.
- Allow ordinary synchronous Python expressions, including calls, attributes,
  comparisons, arithmetic, and nested queries.
- Reject destructuring targets, `:=`, `await`, `yield`, `yield from`, and async
  comprehensions within query source/predicate/selector expression trees in v1.
- Diagnose missing clauses, invalid targets, and incorrect clause order.

Execution semantics:

- Evaluate the source expression once at construction and retain that object.
  Rebinding the original source variable does not replace the retained source.
- Accept Python lists, including subclasses, and native query objects. Raise
  `TypeError` at construction for other sources.
- Defer traversal, filtering, and projection until iteration. Each `iter(query)`
  creates independent iterator state; repeated iterations observe current list
  contents. Do not copy or cache source contents automatically.
- Preserve order and duplicates. Test predicates in written order with Python
  truth testing, short-circuit failures, and project once per surviving element.
- Follow the underlying list iterator's behavior for mutation during iteration.
  Honor a list subclass's `__iter__` override.
- Evaluate the source in the surrounding scope. Each predicate and selector has
  an implicit function scope whose sole parameter is the range variable. Keep
  that variable local and resolve other names with ordinary Python closure and
  late-binding rules, including ordinary nested-function class-scope behavior.
- Raise construction errors immediately and iteration errors when encountered.
  Convert callback-raised `StopIteration` to `RuntimeError` to avoid silent
  exhaustion; close the affected iterator after a callback failure.

`list(query)` materializes results through the native iterator protocol. A public
fluent method API is not part of v1.

## Compiler stages and ownership

| Stage | Custom work | Output / verification |
| --- | --- | --- |
| Lexing | Reuse tokens, punctuation, and parenthesized multiline handling; generate contextual keyword metadata. No new token kinds or lexer algorithm. | Tokenization and identifier/keyword compatibility tests. |
| Parsing | Extend the PEG expression grammar with queries, nesting, clause ordering, and focused invalid-syntax rules. | Query ASTs and positive/negative grammar tests. |
| AST and validation | Add `QueryExpr(target, source, predicates, selector)`; require a `Name` target in store context and preserve expression locations. Validate parsed and manually constructed nodes. | AST traversal, validation, round trips, and compilation from AST. |
| Preprocessing | Visit query children and apply appropriate existing preprocessing while preserving explicit query nodes. Do not replace the public AST with synthetic lambdas. | Correct `ast.parse()` and optimized-AST behavior. |
| Binding | Visit the source in its enclosing scope; create implicit function scopes for predicates and selector, using query-owned clause slots as stable scope keys. | Correct local/global/free-variable and closure behavior. |
| Query IR | Build compiler-owned C plans after preprocessing and symbol-table construction, before instruction generation. Keep plans separately from the public AST. | Deterministic IR inspection and plan validation. |
| Analysis | Build a bounded per-function CFG and compute reaching definitions, dominance, uses, and capture/escape restrictions. | Eligible/ineligible/unknown decisions with reasons and locations. |
| Optimization | Transform proven eligible nested execution into compiler-authorized fused execution in query IR. | Before/after plans and semantic-equivalence checks. |
| Lowering | Compile clause bodies with CPython's function/closure machinery; emit existing import, stack, and call instructions for normal or fused runtime factories. | Disassembly, correct captures, and execution tests. |
| Existing backend | Reuse CPython's instruction CFG, peephole optimizations, stack calculations, jump resolution, location/exception tables, and code-object assembly. | Existing backend regressions remain passing. |
| C runtime | Implement query and iterator types, ordered execution, ownership, cyclic GC, and cleanup. This is execution support, not a new compiler backend. | Runtime correctness and lifetime tests. |

Existing integration points are `Grammar/python.gram`, `Parser/Python.asdl`,
`Python/ast.c`, `Python/ast_preprocess.c`, `Python/symtable.c`, and
`Python/codegen.c`/`Python/compile.c`. Add an internal C planning/analysis module
and a built-in `_linq` C module. Update Python/C unparsers, keyword metadata,
reference documentation, and relevant compiler tests.

The Bazel foreign build regenerates tokens, AST, parser, and keyword outputs
before compilation. Commit their source inputs as complete replacement files
under `patches/`; generated files remain build artifacts.
Query construction must not depend on helper names in user globals.
There is no source-string translation/reparsing step.

## Query IR and analysis

Every query gets a plan, including when fusion is disabled. The initial IR has:

- **Source:** the source expression evaluated at construction.
- **QueryInput:** a plan reference for a directly nested query source. Named
  sources retain binding information; analysis records resolved definitions and
  producer-plan references separately from execution mode.
- **Filter:** an ordered predicate, binding information, and source location.
- **Project:** a selector, binding information, source location, and a flag
  identifying an exact projection of the range variable.
- **Execution mode:** normal nested execution or compiler-authorized fusion.

Ordinary clause expressions remain references to their original ASTs. Existing
CPython code generation compiles their arithmetic, calls, attributes, and other
operations. We are not building an IR or optimizer for every Python expression.

Binding records each query's enclosing symbol entry and class-mangling context.
The planner consumes that registry after symbol analysis. Clause keys use the
addresses of predicate sequence slots and the selector field in their owning
query, distinct from scope-forming lambda/comprehension body expressions.
Compiler plans borrow AST and symbol entries and are freed with the compiler;
only generated code and runtime objects survive compilation.

The function CFG is a high-level analysis model distinct from CPython's later
bytecode-instruction CFG. It supports single-name local assignments, expression
statements, `if`/`else`, returns, and `pass` in synchronous, non-generator
functions. Global/nonlocal declarations contribute binding information without
executable operations; stores outside the function's local bindings are
unsupported. Unsupported constructs make the whole function unknown, including
occurrences after a return. Nested functions are assessed independently. All
otherwise valid query programs still compile without fusion.

Binding retains function AST identities, symbol entries, and mangling context.
Analysis runs after query planning and before instruction generation. Native
bitsets and a worklist solve reaching definitions; predecessor intersection
solves dominance. Parameters have entry definitions, and other locals begin
with unbound definitions. Per-statement replay reads each assignment's right-hand
side before recording its new definition. Unreachable blocks have no solved
reaching or dominance facts.

Deferred clause bodies do not count as eager reads. Symbol-table cell bindings
identify producer captures. Direct aliases and escapes are extra uses of their
reaching definitions; general heap alias analysis is not implemented. Eager
comprehensions, assignment expressions, suspension, unsupported statements,
recognized dynamic namespace access, and explicit frame access make the function
unknown. Deferred clause comprehensions remain executable and are not analyzed
as enclosing-function control flow.

Give local assignments definition IDs and solve reaching definitions:

```text
IN(block)  = union of predecessor OUT sets
OUT(block) = GEN(block) union (IN(block) minus KILL(block))
```

Definition IDs identify analysis facts, not SSA-renamed executable variables.
Check which definition reaches each query source use, whether it dominates the
consumer, and all other uses, aliases, reassignments, or captures of the producer.
Multiple possible producer definitions prevent fusion.

Diagnostics classify pairs as eligible, ineligible, or unknown, with source
locations and supporting definition/use evidence. Direct reflective/dynamic
constructs, unsupported control flow, and unresolvable alias/capture cases are
unknown. Known extra uses and nonidentity projections are ineligible for v1.
Analysis and diagnostics use the same implementation as the compiler pass.
Whole-function unknown takes precedence over individual candidate checks. Known
capture, ambiguity, unbound input, nonidentity projection, extra-use, and
adjacency failures are ineligible for the v1 pattern. A candidate describes
merging a query with its input; ineligible does not mean the query is invalid.
Reassignment is definition-sensitive: uses of earlier/later values are not
attributed to the selected producer. Analysis alone does not fuse queries; the separate opt-in pass applies M5 rewrites.

## Fusion: transformation, proof, and limits

Initial rewrite candidates are consecutive local query assignments in one basic
block, with distinct producer and consumer target names:

```python
def example(values):
    positive = (from x in values where x > 0 select x)
    small = (from y in positive where y < 10 select y)
    return list(small)
```

Require a unique reaching producer definition that dominates the consumer, whose
only use is that consumer's source. Both selectors must be exact identity
projections of their own bound range variables; the consumer must add at least
one filter. Reject unsupported alias/capture cases and analysis regions with
unsupported dynamic/control-flow constructs. Compare bindings, not just matching
identifier spellings. Diagnostics may explain broader cases, but rewriting stays
within this bounded pattern.

Retain the producer's construction and assignment. At consumer construction, the
specialized factory reuses the producer's captured source and original predicate
function objects, appends the consumer predicates, and retains the consumer
selector. It must not reevaluate the original source expression, read a rebound
source variable, recreate producer closures, or call predicates at construction.

Use immutable, non-subclassable native query types and compiler-provided identity
metadata. The fused factory verifies the exact expected query type and identity
metadata; failed checks fall back to ordinary nested construction. Normal
lowering does not silently flatten query inputs when fusion is disabled.

This is an actual compiler-directed optimization: analysis authorizes a changed
IR execution mode and code generation selects the specialized factory. Runtime
composition alone does not satisfy the analysis milestone.

The benefit is removal of the producer projection stage, its iterator-chain
construction, and redundant producer identity-selector calls. Both versions are already lazy and O(n); there is no
intermediate materialized list to eliminate. The first pass does not eliminate
producer allocation, reorder predicates, substitute projections, or fuse general
Python loops. Descriptor copying can increase construction cost/storage; evaluate net runtime
overhead with benchmarks.

Guarantees cover values, source/callback evaluation order, captures, and exceptions
within the supported model. Reflective observation of frames, tracing/profiling
events, object lifetimes, and monkey-patched private runtime internals is outside
that model. Equivalence is limited to the supported execution model.

## Lowering, runtime, and developer interfaces

Private C runtime construction interfaces:

```text
_linq._query(source, predicates, selector, identity_projection)
_linq._fused_query(producer, predicates, selector, identity_projection)
```

The factory accepts a tuple of predicate callables and a selector callable.
Compiler lowering supplies compiled clause functions.
Identity metadata comes from the compiler's binding-aware classification. Query
objects own the source and callbacks; each iterator owns its execution state.
Implement reference-counting, GC traversal/clearing, error/exhaustion cleanup,
and per-interpreter module state following existing CPython conventions.

Normal and guarded fused factories are implemented. Runtime
operator descriptions dispatch to separate native stage constructors and are
distinct from compiler IR. The current language and runtime interfaces are
specified in [LANGUAGE.md](LANGUAGE.md).

The compiler loads factories through import bytecode without introducing helper
bindings in user namespaces. Users need no explicit `_linq` import. The C runtime
traverses inputs and calls clause functions, whose bodies execute as ordinary
Python bytecode. It is compiled once when building CPython, not generated per
query. Do not lower queries to Python `filter`, `map`, comprehensions, or a
third-party library.

The fork-specific `ast.PyCF_LINQ_FUSION` compilation flag is disabled by default.
It affects the submitted compilation unit and nested function bodies, not
ordinary `-O`, dynamically compiled future units, or imported modules. AST-only
compilation keeps explicit query structure; fusion operates on compiler-owned
query IR before instruction generation.

The `scripts/linq.py` driver provides `run`, `ir`, `analyze`, and `dis` subcommands.
IR inspection uses `_linq._inspect` and the same C compiler setup and planner as
normal compilation. The additive `analysis` result exposes CFGs, definitions,
uses, reaching sets, dominators, eligibility reasons, and evidence. Unsupported
functions expose reasons and locations without partial solver facts. `analyze`
prints this analysis result alone. All IDs are deterministic within the report.
The inspector accepts `fusion=False`; the driver accepts `--fusion` and
`--no-fusion`, defaulting to off. The optimizer marks eligible consumer plans
with a fused execution mode and producer reference after analysis. The additive
`optimization` report includes the enabled setting and native before/after query
descriptors for applied rewrites. Code generation uses these modes to select the
normal or guarded factory without rewriting source or recreating producer
closures. The flag is a compile-only option, excluded from inherited code flags;
AST-only requests preserve query structure without applying the optimizer.
Explicitly compile
source for optimized/unoptimized comparisons, without introducing a separate
bytecode-cache mode. Inspection must not execute the inspected program.

## Milestones and reproducible delivery

| ID | Deliverable | Completion gate |
| --- | --- | --- |
| M1 | Reproducible override delivery | Build the pinned upstream archive plus tracked overrides as Bazel artifacts; verify override contents and the resulting interpreter. |
| M2 | Frontend and C runtime | Parser, AST, binding, and direct-factory runtime tests pass. |
| M3 | Unoptimized query IR and lowering | Native filtering/projection executes through explicit IR and normal runtime factories. |
| M4 | Control-flow/data-flow analysis | Shared compiler/tooling analysis explains eligible, ineligible, and unknown pairs with evidence. |
| M5 | Bounded fusion | Proven candidates select fused lowering; equivalence and structural optimization evidence pass. |
| M6 | Runnable demo and code explanation | Readable examples, compact inspection, normal/fused evidence, bounded artifact tests, and preparation material are verified. |

M6 is scoped to a basic runnable demonstration using the existing language and
optimizer, not new operators or wider fusion analysis. The defense slot is
20 minutes: rehearse a five-minute demonstration, five minutes of explanation,
eight minutes for a no-AI edit/build/check, and two minutes of buffer. Build and
prepare dependencies before arrival.

Track three readable examples for base semantics, a proven fusion pair, and
ineligible/unknown cases. Compact driver views use the existing native reports
and compiled instructions; full report modes remain available. Verify examples
against exported and packaged interpreters, including callback timing and actual
projection-work reduction. Commands and expected behavior live in
[the demo guide](../demos/README.md).

Personal rehearsal and compiler study remain separate from automated demo
verification. Passing tests cannot establish that a person can explain or modify
the code without AI. Reference-leak audits, remaining feature coverage, the full
CPython suite, automated macOS/Linux and manual WSL validation, benchmarks, and
clean-environment reconstruction remain unchecked in
[deferred validation](CHECKLIST.md#deferred-validation). Required platform goals
are unchanged; the demo gate does not establish release readiness.

The ignored `cpython/` checkout is optional reference source and remains
unmodified. The build consumes a checksum-pinned CPython v3.14.0 release archive
and complete replacement files under `patches/`, mirroring upstream paths.
Commit those overrides in the outer repository; no nested commits, diff series,
or patch-prefix state are involved.

Bazel declares the source archive and overrides as inputs and produces a
combined source artifact. `rules_foreign_cc` configures, regenerates, compiles,
and installs from a sandbox scratch copy. Native libraries and the regeneration
interpreter are declared dependencies; the platform compiler/SDK remain host
prerequisites. The installed runtime and packaged distribution are final
artifacts. The root `build.sh` delegates to Bazel.

The current delivery gate compares every override with the source artifact and
runs the tracked query suite against the built debug interpreter and extracted
distribution. It covers native syntax execution, frontend/binding/tooling,
IR inspection, driver behavior, and direct runtime semantics. The demo gate
extends this with declared example inputs. Broader semantic and platform
verification remains in the separate validation backlog.

Verification records contain test commands and results, skips/failures, relevant
commits, and known limitations.

## Verification and acceptance

- **Syntax/tooling:** multiline/nested queries, malformed clauses, ordinary uses
  of contextual keywords, AST visitors, Python/C unparsing, round trips,
  compilation from AST, invalid-node rejection, and traceback locations.
- **Results/timing:** empty lists, duplicates, ordered filters, varied projections,
  source queries, explicit-loop references, no callback execution at construction,
  short-circuit order, partial consumption, and exception timing.
- **Binding:** one source evaluation, rebinding/mutation, late captures, target
  isolation, class contexts, and functions returned by selectors.
- **Ownership:** independent iterators, repeated iteration, exhaustion, callback
  failures including `StopIteration`, reference cycles, and debug leak checks.
- **Analysis:** unique/ambiguous reaching definitions, dominance, extra uses,
  projections, reassignment, aliases, captures, branches, and unsupported regions.
- **Fusion:** identical results and callback event logs with the flag on/off;
  preserved source/closure construction timing; before/after IR and disassembly;
  fallback checks and test-only counters proving the intermediate iterator layer
  and producer identity callback are bypassed.
- **Performance:** repeatable microbenchmarks reporting measured overhead changes,
  without requiring every case to be faster or claiming improved complexity.
- **Regression/portability:** dedicated query tests and grammar, AST, compiler,
  symtable, tokenizer, and disassembly suites; broader suite before release;
  automated macOS and Ubuntu/Linux builds plus recorded manual WSL verification.
  Clearly separate real platform results from simulated setup-script checks.

## Deferred extensions

The following families are deferred implementation work, not excluded features.
Filtering (`Where`) and projection (`Select`) remain the basic deliverable.
Ordering is the first suggested extension; each family can be scoped and added
independently once its required core execution and binding capabilities exist.
Neither completion of every other family nor M4-M6 is a blanket prerequisite.

| Family | Deferred functionality |
| --- | --- |
| Multiple sources and flattening | Additional `from` clauses / `SelectMany`. |
| Query bindings and continuations | `let` and query-body `into` continuations. |
| Sorting | `OrderBy`, `OrderByDescending`, `ThenBy`, `ThenByDescending`; multiple ordered keys. |
| Grouping and joins | `GroupBy`, `Join`, `GroupJoin`; `group … by …` and group-join `into`. |
| Set operations | `Distinct`, `Union`, `Intersect`, `Except`. |
| Quantifiers | `Any`, `All`, `Contains`. |
| Aggregation | `Count`, `Sum`, `Min`, `Max`, `Average`, `Aggregate`. |
| Partitioning | `Take`, `Skip`, `TakeWhile`, `SkipWhile`. |
| Element selection | `First`, `Last`, `Single`, `ElementAt`, including default-returning variants. |
| Concatenation and sequence comparison | `Concat`, `SequenceEqual`. |

These names identify capabilities, not a commitment to a public fluent API or
one new keyword per operator. C# query clauses and its broader method-based
operator surface must be distinguished. Each family's Python syntax or invocation
form is specified before its implementation; the v1 syntax remains unchanged.

### Modularity requirements for the basic implementation

- Normalize frontend query nodes into an ordered sequence of typed IR operators.
  Initially implement only source, filter, and projection. Preserve operator
  order and allow additional operator kinds without changing existing kinds.
- Keep AST construction, binding, IR validation, lowering, and native execution
  behind distinct operator-specific handlers. Future clauses can introduce AST
  types and binding rules without extending unrelated filtering code. The current
  `QueryExpr` fields are the basic syntax representation, not a universal schema
  into which all future operators must fit.
- Give each operator explicit input/result categories: sequence, ordered
  sequence, grouping, scalar, or Boolean. Keep streaming, buffering, and terminal
  execution distinct; do not assume every operator returns another lazy iterator.
- Keep runtime ownership, callback invocation, iteration, and error propagation
  reusable. Add native implementations per family rather than a single combined
  filtering/projection execution loop that every extension must replace.
- Treat `_linq._query` and `_linq._fused_query` as the initial private lowering
  entry points. Keep their implementation separate from the internal operator
  representation so later families can add factory/terminal entry points without
  redesigning existing query execution.
- Make fusion opt-in for explicitly supported operator patterns. Operators without
  a fusion proof remain optimization barriers and execute normally; they must
  never be skipped, reordered, or rejected merely because fusion cannot handle
  them. The first filter-fusion pass does not need to optimize future families.
- Before enabling a family, specify its syntax, binding effects, evaluation
  timing, buffering, short-circuit behavior, ordering, duplicates, equality/key
  rules, empty-input results, and exceptions. Record concrete dependencies and
  acceptance cases for that family. Add its AST/IR/lowering/runtime support as one
  extension, with focused verification when that work is authorized.

The basic implementation must meet these extension boundaries without building
unused operator implementations or a general plugin framework. New operators
must compose with existing source/filter/projection execution without rewriting
their semantics. This requirement applies during M2-M3, not after their design
has been fixed around the initial subset.

Additional iterable sources and broader optimizations (projection/loop fusion
and producer-allocation elimination) remain separate deferred work. Database
providers, SQL translation, expression-tree providers, C#'s static type system,
new bytecodes, and general SSA remain outside this agreed implementation.

## References

- [Microsoft LINQ overview](https://learn.microsoft.com/en-us/dotnet/csharp/linq/)
- [CPython v3.14.0 compiler design](https://github.com/python/cpython/blob/v3.14.0/InternalDocs/compiler.md)
- [CPython grammar-change checklist](https://github.com/python/cpython/blob/v3.14.0/InternalDocs/changing_grammar.md)
- [CPython CFG optimizer](https://github.com/python/cpython/blob/v3.14.0/Python/flowgraph.c)
