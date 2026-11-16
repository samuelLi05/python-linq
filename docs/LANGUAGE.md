# Query language and native runtime

## Implementation boundary

The compiler parses, validates, binds, plans, and lowers filtering/projection
queries. Native query expressions execute without an explicit `_linq` import.
Each clause compiles to an ordinary Python function; the native `_linq` runtime
retains those functions and executes them lazily. Normal execution does not fuse
query pipelines. Query-merge analysis and opt-in fusion remain pending.

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
_linq._inspect(tree, filename="<linq>", mode="exec", *, optimize=-1)
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
mangling. Named sources are not assigned producer definitions until M4 analysis.
All current plans use normal execution mode.

The source driver uses the built interpreter:

```bash
./build/python scripts/linq.py ir path/to/program.py
./build/python scripts/linq.py dis path/to/program.py
./build/python scripts/linq.py run path/to/program.py argument
```

Put `--optimize {-1,0,1,2}` before the filename. IR output is JSON. Disassembly
includes nested clause code objects. Only `run` executes the program, with script
arguments and sibling imports available. These commands do not write bytecode
caches for the submitted source file.
