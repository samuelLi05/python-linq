# Query language and native runtime

## Implementation boundary

The compiler parses, validates, binds, plans, and lowers filtering/projection
queries. Native query expressions execute without an explicit `_linq` import.
Each clause compiles to an ordinary Python function; the native `_linq` runtime
retains those functions and executes them lazily. Normal execution does not fuse
query pipelines by default. Control/data-flow analysis reports eligibility, and
explicit opt-in compilation selects guarded filter fusion.

## Syntax

```text
query := "(" "from" NAME "in" expression
             ("where" expression)*
             "select" expression ")"
```

Queries require parentheses, one identifier target, one source, zero or more
ordered predicates, and one final selector. Queries can be nested and used in
expression positions. Clause bodies use Python expressions and Python truth
testing. Ordering and duplicates are preserved.

`from` and `in` are existing Python keywords. `where` and `select` are soft
keywords, so they remain usable as ordinary identifiers elsewhere. Additional
query vocabulary (`let`, `orderby`, `join`, `on`, `equals`, `group`, `by`, `into`,
`ascending`, and `descending`) is registered as soft keywords, but its clauses
remain unsupported. The [operator inventory](PLAN.md#deferred-extensions) defines
the deferred families.

Destructuring, attribute, and subscript query targets are rejected. Assignment
expressions (`:=`), `await`, `yield`, `yield from`, and async comprehensions are
rejected within query source, predicate, and selector expression trees, including
nested expressions. Missing clauses, missing expressions, incorrect clause order,
and unsupported clauses produce syntax errors.

## AST and scope interfaces

`ast.QueryExpr` is an `ast.expr` with these fields:

| Field | Value |
| --- | --- |
| `target` | A required `ast.Name` with `ast.Store` context. |
| `source` | A required expression in load context. |
| `predicates` | An ordered list of expressions in load context; may be empty. |
| `selector` | A required expression in load context. |

The node inherits `lineno`, `col_offset`, `end_lineno`, and `end_col_offset`.
Column offsets follow CPython's UTF-8 byte-offset convention. Manually
constructed nodes must satisfy the same structural constraints when compiled.

`ast.parse()` returns the explicit query node. Optimized AST requests preserve
that node while preprocessing its children. `ast.unparse()` emits a parenthesized
query that can be parsed again; whitespace and original formatting are not
preserved. `ast.walk()`, `NodeVisitor`, and `NodeTransformer` traverse its fields.
CPython's C unparser also supports queries in postponed annotation strings.

The source is bound in the surrounding scope. Each predicate and selector has
an independent implicit function scope whose sole parameter is the query target.
Other names follow normal Python global and closure rules, including late
binding and the ordinary nested-function rules for class namespaces. The target
does not become a local in the surrounding scope.

`symtable.symtable()` exposes these child function scopes as `linq_where` and
`linq_select`. It applies the same query-expression restrictions as AST parsing.
Binding and code generation share query-owned clause-slot keys, distinct from
the expression identities used by nested lambdas and comprehensions.

## Private runtime factory

```python
_linq._query(source, predicates, selector, identity_projection, /)
```

| Argument | Contract |
| --- | --- |
| `source` | A list, a list subclass, or an exact query from the same native module instance. |
| `predicates` | A tuple of callables, each receiving one element. |
| `selector` | A callable receiving each surviving element. |
| `identity_projection` | A Boolean supplied by the compiler as identity-projection metadata. |

Invalid inputs raise `TypeError` during construction. The factory retains the
source and callbacks without obtaining a source iterator or invoking callbacks.
The result is immutable and non-subclassable; its native type cannot be constructed
directly. This is compiler support, not a public fluent query API.

The runtime can be exercised directly with the built interpreter:

```python
from _linq import _query

query = _query([1, 2, 3], (lambda x: x > 1,), lambda x: x * 2, False)
assert list(query) == [4, 6]
```

`iter(query)` obtains a source iterator and builds independent filter/projection
stages. List subclasses use their normal `__iter__` implementation, including its
side effects and errors. Predicates and selectors are not called until elements
are requested. Each element passes through predicates in order; the first false
result skips the remaining predicates and the selector. The selector runs once
per surviving element. Identity metadata does not bypass the selector.

Each iteration starts afresh. The query retains the source object supplied at
construction: rebinding a source variable does not replace it, and mutations to
that object remain visible according to its iterator's behavior. No source copy
or result cache is created. A query source remains a separate nested pipeline;
ordinary construction does not fuse queries.

Source errors propagate at iterator creation or advancement. Predicate, truth
testing, and selector errors propagate during advancement. A `StopIteration`
raised by a callback or its truth testing becomes `RuntimeError` with the
original exception as its cause; source exhaustion remains normal exhaustion.
Failure exhausts the affected iterator. Other iterators and new iterations
remain usable. Reentrant advancement of an already executing iterator raises
`RuntimeError`.

Queries and stages own their required source/callback references. Exhaustion,
failure, and deallocation release iterator state; a surviving query continues to
own its configuration. Native objects participate in cyclic garbage collection.
Module state and native types are per interpreter. The module requires the GIL.

## Extension boundaries

Filter and projection are separate native iterator stages. Later streaming
operators can add stages; buffering operators can retain their own state; scalar
and Boolean terminal operators can consume an upstream iterator through separate
entry points. These changes do not require replacing existing filter/projection
behavior.

Runtime operator descriptions are distinct from the compiler-owned query IR.
The current `QueryExpr` fields describe the basic syntax rather than a universal
schema for future clauses. Fusion, new operators, additional source kinds, and a
public invocation form for those operators remain separately scoped work.

## Compiler inspection

```python
_linq._inspect(tree, filename="<linq>", mode="exec", *, optimize=-1, fusion=False)
```

This private interface accepts a matching `ast.Module`, `ast.Expression`, or
`ast.Interactive` for `exec`, `eval`, or `single`. Optimization accepts `-1`
(interpreter setting), `0`, `1`, or `2`. It validates and converts the AST,
preprocesses it, builds symbols, and creates plans through the same compiler
setup as normal compilation. It does not generate instructions or execute the
inspected program, and it does not mutate the supplied Python AST.

The result is a dictionary with a `queries` list in binding traversal order.
Each query contains a deterministic `id`, `owner` scope, `location`, execution
`mode`, and ordered `operators`. Scope descriptions contain an `id`, `name`, and
`location`. IDs are inspection-local integers, not memory addresses. Locations
are `(lineno, col_offset, end_lineno, end_col_offset)` with UTF-8 byte columns.

Operators report their kind, input/result categories, expression-node kind, and
location. Sources have no operator input; filters and projections consume and
produce sequences.
A named source includes its normalized binding name and scope category; a direct
query source includes `input_plan`. Filters and projections include scope,
parameter, and sorted free-variable captures. Projections include `identity`.
Identity means a load of the clause's own parameter, including class-name
mangling. The additive `analysis` result records resolved producer definitions
and query-plan references for named sources. The source operator's `input_plan`
continues to describe directly nested query syntax. Normal plans have mode
`normal`; eligible consumers selected with `fusion=True` have mode `fused` and a
`producer_plan` reference. Source expressions and clause ASTs are not substituted.

The additive `optimization` dictionary has `enabled` and `rewrites` fields.
Each rewrite contains `before` and `after` query descriptors with the same ID;
only authorized plans appear. Before descriptors use normal execution and after
descriptors include the fused mode/producer reference. M4 analysis and its
eligibility decisions remain the same with fusion off or on.

The source driver uses the built interpreter:

```bash
./build/python scripts/linq.py ir path/to/program.py
./build/python scripts/linq.py analyze path/to/program.py
./build/python scripts/linq.py dis path/to/program.py
./build/python scripts/linq.py run path/to/program.py argument
```

Put `--optimize {-1,0,1,2}` and `--fusion`/`--no-fusion` before the filename.
Fusion defaults to off. IR and analysis output are JSON. Disassembly
includes nested clause code objects. Only `run` executes the program, with script
arguments and sibling imports available. These commands do not write bytecode
caches for the submitted source file.

## Query-merge analysis

The inspector's `analysis` dictionary contains `functions` and `candidates`.
`analyze` prints that dictionary without executing the submitted program.

A function record has a report-local `id`, scope `name`, source `location`,
`status`, and `reasons`. Supported functions have status `analyzed` and include:

- `entry`/`exit` block IDs and `blocks` with statement locations, sorted edges,
  reachability, `gen`, `kill`, `in`, `out`, and `dominators` sets.
- `bindings` with normalized names, capture flags, and definition IDs.
- `definitions` with binding, kind (`parameter`, `unbound`, or `assignment`),
  block, statement position, query-plan reference, and location. Entry definitions
  have null block/position fields; their locations identify the function.
- `uses` with binding, kind (`read`, `query_source`, `alias`, or `escape`), block,
  position, optional query reference, location, and reaching definition IDs.

Binding, definition, use, and block IDs are local to their function. Function and
query-plan IDs are local to the whole report. Sets are sorted integer lists.
Unreachable blocks have empty `in`, `out`, and dominance sets. `GEN` retains the
last definition of each assigned binding; `KILL` excludes those generated facts.

Each candidate considers a query as a consumer of its input. Its `consumer`
references the query plan. It reports `status` (`eligible`, `ineligible`, or
`unknown`), a stable `reason` code, location, function ID, and available evidence:
source use, reaching definitions, producer definition/plan, extra use, dominance,
and adjacency. Null IDs mean no corresponding fact is established. A first query
whose source is a parameter is normally ineligible as a merge consumer; that
verdict does not prevent it serving as another query's producer.

Eligibility requires consecutive distinct-local-name query assignments in one
block, one initialized reaching producer that dominates the consumer, no other
producer uses or captures, identity selectors, and at least one consumer filter.
Reasons distinguish ambiguity, unbound paths, nonquery definitions, capture,
alias/extra uses, transformed projections, missing filters, and adjacency failure.

Unsupported statements, targets, eager comprehensions, binding/suspension
expressions, async/generator functions, recognized dynamic namespace operations,
and explicit frame access make the whole function `unknown`. Such function
records expose reason codes and locations and omit partial CFG/solver results.
The CFG describes normal flow and explicit returns; uncaught exceptions leave
the function, and exception handlers are outside this subset. This includes
unreachable unsupported statements. Ordinary query execution is
preserved, and nested function bodies are assessed independently. Module, class,
lambda, and implicit clause scopes are outside v1 CFG analysis.

The namespace recognizer conservatively flags loads named `exec`, `eval`,
`locals`, `globals`, `vars`, and `dir`, and attributes named `_getframe`,
`currentframe`, `f_locals`, and `f_globals`, even when shadowed. Arbitrary callee
purity, hidden caller-frame reflection, tracing observations, and monkey-patched
private runtime internals are outside the supported proof model. Query callbacks
remain deferred; unrelated captures do not prevent eligibility.

The analysis does not rename variables, insert SSA phi nodes, or enable fusion
itself. A separate optimizer selects guarded fused execution when explicitly
requested; ineligible and unknown plans remain normal.

## Opt-in guarded fusion

```python
import ast
code = compile(source, filename, "exec", flags=ast.PyCF_LINQ_FUSION,
               dont_inherit=True)
```

`PyCF_LINQ_FUSION` is a fork-specific compilation flag, disabled by default and
independent of `-O`/`optimize`. It applies to the submitted compilation unit and
nested function bodies. It is not placed in code-object flags, so later
`compile()`, `eval()`, `exec()`, and imported modules do not implicitly inherit it.
AST-only/optimized-AST requests keep explicit query nodes and do not perform
fusion. Supplying an AST to ordinary `compile()` can enable the flag explicitly.

The optimizer consumes eligible M4 decisions after binding, planning, and
analysis. It retains producer assignments, source evaluation, and callback
construction. Code generation loads `_linq._fused_query` for selected consumers
and `_linq._query` for normal plans; no helper bindings enter user namespaces.

The specialized private entry has the same positional argument shape:

```python
_linq._fused_query(producer, predicates, selector, identity_projection, /)
```

Validation is shared with the normal factory, in the same order. Guards require
an exact native query from the current module, identity metadata for both sides,
and a supported producer filter/projection layout. Guard failure uses ordinary
nested construction; invalid arguments and allocation errors still propagate.
Identity metadata is supplied by the compiler, not inferred by examining arbitrary
callable bytecode in the factory.

A successful fused construction retains the producer's captured source and
original filter callbacks, appends consumer predicates, and retains the consumer
selector. It omits the producer identity projection. No callbacks or source
iteration run during construction. An unapproved query further upstream remains
a separate pipeline. Already fused producers can participate in proven chains.

This uses the same query and iterator types, GC support, error handling, and
independent-iteration behavior. The producer object and selector function are
still constructed; the consumer selector still executes. The reduction is a
producer projection stage, its identity calls, and producer iterator-chain
construction. Both paths remain lazy with the same traversal complexity.
Descriptor copying can increase construction cost/storage; profitability is
measured separately. The proof-model exclusions documented above still apply.

```bash
./build/python scripts/linq.py ir --fusion path/to/program.py
./build/python scripts/linq.py dis --fusion path/to/program.py
./build/python scripts/linq.py run --no-fusion path/to/program.py
./build/python scripts/linq.py run --fusion path/to/program.py
```

Inspection and disassembly do not execute the submitted program. The driver
explicitly selects compilation flags and does not write caches for that source.

## Debug execution counters

Debug builds expose `_linq._test_counters(*, enabled=None, reset=False)` for
structural tests. State is per interpreter and counting is off by default.
`enabled=None` preserves the setting; reset clears counts before returning the
snapshot. Release builds omit this private testing method.

The snapshot contains `enabled`, `normal_queries`, `fused_queries`,
`fusion_fallbacks`, `query_iterators`, `filter_iterators`, `project_iterators`,
and `project_calls`. Construction/allocation counters count successful events;
projection calls count callback invocation attempts. Fallbacks count guard
mismatches. Counts saturate at the native signed-size maximum.

For two identity queries over `[1, 2, 3, 4]`, with a producer filter accepting
all items and a consumer filter accepting even items, full consumption constructs
two query objects in both modes. Iterator-chain construction falls from two to
one, projection-stage allocation from two to one, and projection calls from six
to two. These are structural reductions, not elapsed-time benchmark results.

## Compact inspection views

The `ir`, `analyze`, and `dis` driver commands accept `--summary`; omitting it
preserves full JSON or disassembly output. Summary commands do not execute the
submitted program.

- IR rows identify query/owner/location, mode, source, filter count, identity
  metadata, and selected producer. Applied rewrites show before/after modes.
- Analysis rows report each query as a merge consumer, with its native status,
  reason code, producer when resolved, and reaching-definition IDs when known.
  Unsupported function reasons are also shown.
- Disassembly rows come from compiled instructions and nested code objects.
  They report actual runtime-factory imports and clause parameters/free variables.

These views do not repeat semantic analysis in Python or change compiler flags,
eligibility, or execution behavior. Runnable examples and expected observations
are in [the demo guide](../demos/README.md).
