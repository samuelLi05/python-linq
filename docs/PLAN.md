# Native LINQ for Python: implementation plan

This is the agreed implementation scope. Follow the stable tasks and verification
records in [CHECKLIST.md](CHECKLIST.md); use [SETUP.md](SETUP.md) for build commands.
The project is at environment setup: no LINQ feature or optimizer is implemented.

## Goal, priorities, and boundaries

Extend CPython **v3.14.0** with native declarative queries over lists. This is a
compiler project involving grammar, ASTs, binding, a query intermediate
representation (IR), control-flow/data-flow analysis, lowering, and a C runtime.

Delivery order:

1. Native filtering/projection with correct deferred execution.
2. Query-merge analysis with explanations.
3. A bounded, opt-in fusion optimization that changes generated execution.
4. Additional LINQ features only after those checkpoints are complete.

The first complete language-feature checkpoint is useful on its own; the expanded
project includes analysis and actual fusion before richer operators. There is no
fixed deadline. Keep each milestone demonstrable and record unfinished work.

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

This syntax is specified, not yet implemented. The first version accepts:

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

```text
Source with native query syntax
  -> existing tokenizer + extended PEG parser
  -> explicit query AST
  -> validation and name/scope analysis
  -> query IR + function control-flow model
  -> definition/use analysis + optional fusion
  -> closure and C-runtime-call lowering
  -> CPython instruction sequence
  -> existing instruction CFG optimizations and assembler
  -> bytecode executing with our built-in C query runtime
```

| Stage | Custom work | Output / verification |
| --- | --- | --- |
| Lexing | Reuse tokens, punctuation, and parenthesized multiline handling; generate contextual keyword metadata. No new token kinds or lexer algorithm. | Token walkthroughs and identifier/keyword compatibility tests. |
| Parsing | Extend the PEG expression grammar with queries, nesting, clause ordering, and focused invalid-syntax rules. | Query ASTs and positive/negative grammar tests. |
| AST and validation | Add `QueryExpr(target, source, predicates, selector)`; require a `Name` target in store context and preserve expression locations. Validate parsed and manually constructed nodes. | AST traversal, validation, round trips, and compilation from AST. |
| Preprocessing | Visit query children and apply appropriate existing preprocessing while preserving explicit query nodes. Do not replace the public AST with synthetic lambdas. | Correct `ast.parse()` and optimized-AST behavior. |
| Binding | Visit the source in its enclosing scope; create implicit function scopes for predicates and selector, using original expression nodes as stable scope keys. | Correct local/global/free-variable and closure behavior. |
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

Regenerate AST, parser, and keyword outputs with `make regen-ast`,
`make regen-pegen`, and `make regen-keyword`; commit generated outputs with their
inputs. Query construction must not depend on helper names in user globals.
There is no source-string translation/reparsing step.

## Query IR and analysis

Every query gets a plan, including when fusion is disabled. The initial IR has:

- **Source:** the source expression evaluated at construction.
- **QueryInput:** a reference to a query-producing local definition.
- **Filter:** an ordered predicate, binding information, and source location.
- **Project:** a selector, binding information, source location, and a flag
  identifying an exact projection of the range variable.
- **Execution mode:** normal nested execution or compiler-authorized fusion.

Ordinary clause expressions remain references to their original ASTs. Existing
CPython code generation compiles their arithmetic, calls, attributes, and other
operations. We are not building an IR or optimizer for every Python expression.

The function CFG is a high-level analysis model distinct from CPython's later
bytecode-instruction CFG. Initially support plain assignments, expression
statements, `if`/`else`, returns, and `pass` in ordinary synchronous,
non-generator functions. Unsupported constructs produce conservative unknown
results; all otherwise valid query programs still compile without fusion.

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
Analysis and diagnostics must use the same implementation as the compiler pass.

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

```text
Unoptimized:
  list iterator -> producer filters -> producer identity projection
                -> intermediate query iterator
                -> consumer filters -> consumer identity projection

Fused:
  list iterator -> producer filters -> consumer filters
                -> consumer identity projection
```

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

The benefit is removal of an intermediate iterator layer and redundant producer
identity-selector calls. Both versions are already lazy and O(n); there is no
intermediate materialized list to eliminate. The first pass does not eliminate
producer allocation, reorder predicates, substitute projections, or fuse general
Python loops. Measure speedups rather than assuming them.

Guarantees cover values, source/callback evaluation order, captures, and exceptions
within the supported model. Reflective observation of frames, tracing/profiling
events, object lifetimes, and monkey-patched private runtime internals is outside
that model. Do not claim equivalence for arbitrary dynamic Python programs.

## Lowering, runtime, and developer interfaces

Private C runtime construction interfaces:

```text
_linq._query(source, predicates, selector, identity_projection)
_linq._fused_query(producer, predicates, selector, identity_projection)
```

Predicates are a tuple of compiled functions; selectors are compiled functions.
Identity metadata comes from the compiler's binding-aware classification. Query
objects own the source and callbacks; each iterator owns its execution state.
Implement reference-counting, GC traversal/clearing, error/exhaustion cleanup,
and per-interpreter module state following existing CPython conventions.

The compiler loads factories through import bytecode without introducing helper
bindings in user namespaces. Users need no explicit `_linq` import. The C runtime
traverses inputs and calls clause functions, whose bodies execute as ordinary
Python bytecode. It is compiled once when building CPython, not generated per
query. Do not lower queries to Python `filter`, `map`, comprehensions, or a
third-party library.

Add the fork-specific `ast.PyCF_LINQ_FUSION` compilation flag, disabled by default.
It affects the submitted compilation unit and nested function bodies, not
ordinary `-O`, dynamically compiled future units, or imported modules. AST-only
compilation keeps explicit query structure; fusion operates on compiler-owned
query IR before instruction generation.

Provide a developer driver for source execution, IR inspection, and eligibility
explanations using the same C planning/analysis implementation. Explicitly compile
source for optimized/unoptimized comparisons, without introducing a separate
bytecode-cache mode. Inspection must not execute the inspected program.

## Milestones and reproducible delivery

| ID | Deliverable | Completion gate |
| --- | --- | --- |
| M0 | Baseline and compiler walkthrough | Pinned debug build and baseline recorded; trace comprehension, generator, and lambda through tokens, AST, scopes, instructions, CFG, and execution. |
| M1 | Reproducible patch delivery | Reconstruct the development changes from a clean pinned checkout using the tracked patch series. |
| M2 | Frontend and C runtime | Syntax/AST/binding and independently tested runtime work; native end-to-end execution may still be incomplete. |
| M3 | Unoptimized query IR and lowering | Native query programs execute through explicit IR and normal runtime factories. First complete language-feature checkpoint. |
| M4 | Control-flow/data-flow analysis | Shared compiler/tooling analysis explains eligible, ineligible, and unknown pairs with evidence. |
| M5 | Bounded fusion | Proven candidates select fused lowering; equivalence and structural optimization evidence pass. |
| M6 | Integration and explanation | Platform, regression, ownership, performance, and author-walkthrough evidence recorded. |

The nested `cpython/` checkout retains its development history. Because the outer
repository ignores it, store ordered patches against v3.14.0 in the outer
repository, including generated files. Record base and checkpoint commits.

Setup must identify already-applied patches and apply missing ones only to a
clean checkout matching the pinned base and expected patch prefix. Preserve
local work and refuse incompatible states with actionable diagnostics. If all
required patches are already present, ordinary local development must remain
possible. Document patch export, replay, and conflict recovery; validate clean
reconstruction. Automatic patch replay requires a Git checkout; document the
source-archive path's limitation instead of assuming it has Git history.

Each checklist task records implementation separately from milestone verification.
For each milestone record changed areas, rationale, an example, test commands,
results/skips/failures, relevant commits, and unresolved limitations. New features
wait until the current checkpoint can be demonstrated and explained.

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
  symtable, tokenizer, and disassembly suites; broader suite before demonstration;
  automated macOS and Ubuntu/Linux builds plus recorded manual WSL verification.
  Clearly separate real platform results from simulated setup-script checks.

## Deferred extensions

After M6, re-scope one extension at a time. Ordering is the first candidate;
multiple sources, `let`, grouping, joins, continuations, additional iterable types,
and broader operators follow only with separate semantics and tests. Database
providers, SQL translation, expression-tree providers, C#'s static type system,
new bytecodes, and general SSA remain outside this agreed implementation.

This plan replaces the earlier detection-only bonus analysis and Python-runtime
method-API scope. Analysis and opt-in fusion are now planned project milestones.

## References

- [Microsoft LINQ overview](https://learn.microsoft.com/en-us/dotnet/csharp/linq/)
- [CPython v3.14.0 compiler design](https://github.com/python/cpython/blob/v3.14.0/InternalDocs/compiler.md)
- [CPython grammar-change checklist](https://github.com/python/cpython/blob/v3.14.0/InternalDocs/changing_grammar.md)
- [CPython CFG optimizer](https://github.com/python/cpython/blob/v3.14.0/Python/flowgraph.c)
