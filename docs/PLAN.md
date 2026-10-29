# Native LINQ for Python: project plan

## Goal and scope

Extend CPython itself with declarative queries over lists. The core target is
filtering and projection through deferred, repeatable query objects. Full LINQ
to Objects is a stretch goal; detecting mergeable queries is a separate bonus.

Reference baseline: CPython v3.14.0, selected for reproducible study rather than
as a claim about the latest release. Compiler changes involve C, PEG grammar,
AST definitions, Python runtime code, and CPython's test framework.

## Working language design

```python
scores = [97, 92, 81, 60]
passing = (
    from score in scores
    where score > 80
    select score
)
print(list(passing))  # [97, 92, 81]
```

This syntax is proposed, not implemented. Parentheses are the working expression
boundary; declarative clause structure is the central requirement.

Core subset:

- One `from` clause, binding a single identifier.
- Zero or more `where` clauses and one final `select` clause.
- Ordinary synchronous Python expressions in clauses.
- Lists and query objects as required sources.
- Query expressions usable in assignments, arguments, and return values.
- Destructuring, asynchronous clauses, and assignment expressions in clause
  bodies deferred until their scope rules have been reviewed.

Execution contract:

- Evaluate the source expression once when constructing the query and retain
  that object. Rebinding its original variable does not replace the source.
- Defer traversal, filtering, and projection until iteration.
- Start a fresh iterator on each iteration over a list-backed query; observe
  current list contents and do not cache results automatically.
- Preserve source order and duplicates. Short-circuit filters in clause order
  and project once per surviving element.
- Keep range variables local. Use ordinary Python closure behavior for other
  referenced variables. Propagate exceptions when execution reaches them.
- Any future general iterable support inherits the source's repeatability;
  wrapping a consumed generator does not restore its elements.

Bundle a `linq` module exposing `from_iterable(source)`, `Query.where(predicate)`,
`Query.select(selector)`, and `Query.__iter__()`. Operators create new queries
without modifying predecessors. Native syntax shares these runtime semantics;
`list(query)` initially provides materialization.

## Compiler roadmap

| Stage | Existing locations | Planned integration |
| --- | --- | --- |
| Lexing | `Parser/lexer/`, `Parser/tokenizer/`, `Grammar/Tokens` | Reuse existing tokens and punctuation; introduce clause words as contextual soft keywords. |
| Parsing | `Grammar/python.gram` | Add parenthesized query expressions, clauses, and targeted syntax errors; regenerate the parser. |
| AST | `Parser/Python.asdl`, `Python/ast.c` | Add a query expression with ordered clause structure, source locations, and validation of parsed and manually constructed nodes. |
| Preprocessing | `Python/ast_preprocess.c` | Visit query children and prepare compiler-owned callable ASTs for clause bodies before symbol-table analysis. |
| Scopes | `Python/symtable.c` | Analyze callable scopes, range variables, surrounding names, and closures. |
| Code generation | `Python/codegen.c`, `Python/compile.c` | Evaluate source and construct the runtime query using closures and existing import/call instructions, without user-global helper variables. |
| CFG optimization | `Python/flowgraph.c` | Reuse existing instruction-level optimizations. Keep query analysis at the AST level, where clauses remain identifiable. |
| Assembly/execution | `Python/assemble.c`, existing interpreter | Reuse bytecode instructions; implement deferred execution in the bundled runtime. No new opcode is planned initially. |
| Tooling | `Lib/ast.py`, `Python/ast_unparse.c`, keyword metadata, tests and reference documentation | Add inspection, traversal, unparsing, round trips, diagnostics, and documentation. |

Preserve explicit query structure in the parsed AST for tools and analysis.
Prepare callable nodes during compilation and retain the outer query node for
runtime construction. Do not convert source to strings and reparse it.

Regenerate derived AST, parser, and keyword files with `make regen-ast`,
`make regen-pegen`, and `make regen-keyword`. No new token kinds or public C API
are planned for the core feature. Existing Python identifiers such as `where`
and `select` must continue to work outside clause positions.

## Milestones

1. **Environment and investigation:** pin and build unchanged CPython, record
   baseline tests, and trace one comprehension and generator expression through
   tokens, AST, scopes, instructions, and execution. Document the walkthrough
   before feature changes.
2. **Runtime:** implement and test the small repeatable `Query` abstraction and
   method API independently of syntax.
3. **Native feature:** integrate parsing, ASTs, preprocessing, scope analysis,
   code generation, unparsing, and the tested runtime. This is the core project
   completion target.
4. **Full LINQ to Objects stretch:** add multiple sources/flattening and `let`,
   stable ordering, grouping, joins/group joins, and `into` continuations. Then
   expand partitioning, set operations, aggregation, element selection,
   quantifiers, sequence comparison, generation, and conversion.
5. **Optional analysis:** build an opt-in AST analysis tool with per-function
   control-flow and definition/use information to identify mergeable queries.

Use the .NET 10 `Enumerable` API as a fixed stretch coverage catalogue. Maintain
a parity table with Python names, execution timing, tests, and adaptations for
Python types and exceptions. Ordering/grouping remain deferred but may buffer
input during iteration; scalar terminal operators execute when called.

Database providers, SQL translation, external expression-tree providers, and
C#'s static type system are outside this project's chosen stretch scope.

## Optional merge analysis

```python
positive = (from x in values where x > 0 select x)
small = (from y in positive where y < 10 select y)
```

Initially prove eligibility for simple local assignments in straight-line code,
with identity projections in both queries and only further filtering in the
consumer. Check additional uses, reassignment, aliases, and closure capture.
Report eligible pairs with locations and reasons; classify unsupported dynamic
cases as unknown. Start with detection only, without rewriting code.

Any later rewrite must preserve source binding, captures, exceptions, predicate
order, and deferred execution. Runtime composition of filters does not by itself
constitute the requested control-flow/data-flow analysis.

## Tests and acceptance

- Syntax: single-line/multiline queries, nesting, missing/misplaced clauses,
  malformed targets, and compatibility with imports and comprehensions.
- Results: empty lists, duplicates, multiple filters, different projection types,
  and source order, compared with straightforward Python references.
- Timing: event logs/counters establish no predicate/projector calls during
  construction, correct short-circuit order, partial consumption, and exception
  timing.
- Repeatability: repeated complete iterations and independently advancing
  iterators; list changes before enumeration; no cached iterator state.
- Binding: source expression runs once, source rebinding, closures, and range
  variable isolation.
- Compiler tools: AST visitors, inspection, unparse/parse round trips,
  compilation from AST, invalid-node rejection, and traceback locations.
- Analysis: accepted simple chains and rejection/unknown results for additional
  uses, transformations, aliases, captures, branches, loops, and reassignments.
- Regression: focused feature tests, relevant grammar/AST/compiler/symbol-table/
  tokenizer/disassembly tests, and the broader suite before final demonstration.

Core success means a reproducible build, correct native filtering/projection
semantics, relevant passing tests, and an author walkthrough of every changed
stage. Report baseline failures separately and document unfinished stretch work.

## References

- https://learn.microsoft.com/en-us/dotnet/csharp/linq/
- https://learn.microsoft.com/en-us/dotnet/csharp/linq/standard-query-operators/
- https://learn.microsoft.com/en-us/dotnet/api/system.linq.enumerable?view=net-10.0
- https://github.com/python/cpython/blob/v3.14.0/InternalDocs/compiler.md
- https://github.com/python/cpython/blob/v3.14.0/InternalDocs/changing_grammar.md
