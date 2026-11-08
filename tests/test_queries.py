"""Functional checks for the current query frontend and built interpreter."""

import ast
import gc
import subprocess
import symtable
import sys
import unittest
import weakref

from _linq import _query


class QueryTests(unittest.TestCase):
    def test_supported_queries_are_accepted(self):
        for source in (
            '(from x in values select x)',
            '(from x in values where x > 0 where x < 10 select x * 2)',
            '(from x in values\n where x > 0\n select x)',
            '(from y in (from x in values select x) select y)',
            '(from x in values select (from y in x select y))',
            '(from where in select where where > 0 select where)',
            '(from x in values select x)\n(outside := 1)',
        ):
            with self.subTest(source=source):
                ast.parse(source)

    def test_malformed_queries_are_rejected(self):
        for source in (
            '(from in values select x)',
            '(from x values select x)',
            '(from x in )',
            '(from x, y in values select x)',
            '(from x in values where x > 0)',
            '(from x in values where select x)',
            '(from x in values select )',
            '(from x in values select x where x > 0)',
            '(from x in values select x select x)',
        ):
            with self.subTest(source=source):
                with self.assertRaises(SyntaxError):
                    ast.parse(source)

    def test_forbidden_query_expressions_are_rejected(self):
        for expression in (
            '(captured := x)', 'await f()', '(yield x)', '(yield from values)',
            '[y async for y in values]',
            '(from y in values select (captured := y))',
        ):
            for source in (
                f'(from x in {expression} select x)',
                f'(from x in values where {expression} select x)',
                f'(from x in values select {expression})',
            ):
                with self.subTest(source=source):
                    with self.assertRaises(SyntaxError):
                        ast.parse(source)
                    with self.assertRaises(SyntaxError):
                        symtable.symtable(source, '<query>', 'exec')

    def test_deferred_operators_are_rejected(self):
        for clause in (
            'let y = x select y', 'orderby x descending select x',
            'join y in other on x equals y select x', 'group x by x.key',
            'select x into y select y', 'from y in other select y',
        ):
            with self.subTest(clause=clause):
                with self.assertRaises(SyntaxError):
                    ast.parse('(from x in values ' + clause + ')')

    def test_query_program_fails_before_executing(self):
        result = subprocess.run(
            [sys.executable, '-c', '''
print("started")
query = (from x in [1, 2, 3] where x > 1 select x * 2)
print(list(query))
'''], capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn('SyntaxError', result.stderr)
        self.assertIn('LINQ query execution is not implemented yet', result.stderr)

    def test_ordinary_program_executes_correctly(self):
        result = subprocess.run(
            [sys.executable, '-c', '''
where = [-2, 0, 3]
let = join = group = into = orderby = on = equals = by = ascending = descending = 1
def select(values):
    return [x * 2 for x in values if x > 0]
print(select(where))
'''], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '[6]\n')
        self.assertEqual(result.stderr, '')


class QueryBindingTests(unittest.TestCase):
    def test_source_and_clause_scopes(self):
        module = symtable.symtable('''
def query(values, limit, scale):
    return (from x in values where x > limit where x < scale select x * scale)
''', '<binding>', 'exec')
        function = module.lookup('query').get_namespace()
        self.assertTrue(function.lookup('values').is_referenced())
        self.assertNotIn('x', function.get_identifiers())
        first, second, selector = function.get_children()
        self.assertEqual([scope.get_name() for scope in (first, second, selector)],
                         ['linq_where', 'linq_where', 'linq_select'])
        for scope in (first, second, selector):
            self.assertEqual(scope.get_type(), symtable.SymbolTableType.FUNCTION)
            self.assertEqual(scope.get_parameters(), ('x',))
            self.assertTrue(scope.lookup('x').is_parameter())
        self.assertEqual(first.get_frees(), ('limit',))
        self.assertEqual(second.get_frees(), ('scale',))
        self.assertEqual(selector.get_frees(), ('scale',))

    def test_target_can_shadow_source(self):
        module = symtable.symtable('''
def query(x):
    return (from x in x select x)
''', '<binding>', 'exec')
        function = module.lookup('query').get_namespace()
        selector, = function.get_children()
        self.assertTrue(function.lookup('x').is_parameter())
        self.assertTrue(function.lookup('x').is_referenced())
        self.assertEqual(selector.get_parameters(), ('x',))
        self.assertEqual(selector.get_frees(), ())

    def test_global_and_nonlocal_captures(self):
        module = symtable.symtable('''
limit = 10
def global_query(values):
    global limit
    return (from x in values where x > limit select x)
def outer(limit):
    def nonlocal_query(values):
        nonlocal limit
        return (from x in values where x > limit select x)
    return nonlocal_query
''', '<binding>', 'exec')
        global_query = module.lookup('global_query').get_namespace()
        outer = module.lookup('outer').get_namespace()
        global_predicate = global_query.get_children()[0]
        self.assertTrue(global_predicate.lookup('limit').is_global())
        nonlocal_query = outer.lookup('nonlocal_query').get_namespace()
        self.assertTrue(nonlocal_query.lookup('limit').is_nonlocal())
        nonlocal_predicate = nonlocal_query.get_children()[0]
        self.assertTrue(nonlocal_predicate.lookup('limit').is_free())

    def test_nested_query_captures_outer_target(self):
        module = symtable.symtable('''
def query(values, limit):
    return (from x in values select (from y in x where y > limit select x + y))
''', '<binding>', 'exec')
        function = module.lookup('query').get_namespace()
        outer_selector, = function.get_children()
        predicate, inner_selector = outer_selector.get_children()
        self.assertTrue(outer_selector.lookup('x').is_parameter())
        self.assertTrue(outer_selector.lookup('x').is_referenced())
        self.assertEqual(predicate.get_parameters(), ('y',))
        self.assertTrue(predicate.lookup('limit').is_free())
        self.assertTrue(inner_selector.lookup('x').is_free())
        self.assertTrue(inner_selector.lookup('y').is_parameter())

    def test_class_locals_are_not_clause_closures(self):
        module = symtable.symtable('''
class Queries:
    values = [1, 2, 3]
    limit = 2
    query = (from x in values where x > limit select x)
''', '<binding>', 'exec')
        class_scope = module.lookup('Queries').get_namespace()
        predicate, selector = class_scope.get_children()
        self.assertTrue(class_scope.lookup('values').is_referenced())
        self.assertTrue(class_scope.lookup('limit').is_local())
        self.assertTrue(predicate.lookup('limit').is_global())
        self.assertEqual(predicate.get_frees(), ())
        self.assertEqual(selector.get_parameters(), ('x',))
        self.assertNotIn('x', class_scope.get_identifiers())


class QueryToolingTests(unittest.TestCase):
    def test_function_type_query_validation(self):
        for source in (
            '(int) -> (from x in values select (captured := x))',
            '((from x in values select (await f()))) -> int',
        ):
            with self.subTest(source=source):
                with self.assertRaises(SyntaxError):
                    ast.parse(source, mode='func_type')
        source = '(int) -> (from x in values select x)'
        tree = ast.parse(source, mode='func_type')
        self.assertEqual(ast.dump(ast.parse(ast.unparse(tree), mode='func_type')), ast.dump(tree))

    def test_unparse_round_trips(self):
        for source in (
            '(from x in values select x)',
            '(from x in values where x > 0 where x < 10 select x * 2)',
            '(from y in (from x in values select x) select y)',
            '(from x in values select (from y in x select y))',
            '(from x in (left if flag else right) select lambda y: x + y)',
            'consume((from x in values select x))',
            'f"{(from x in values select x)}"',
            '(from where in select where where > 0 select where)',
        ):
            with self.subTest(source=source):
                original = ast.parse(source)
                restored = ast.parse(ast.unparse(original))
                self.assertEqual(ast.dump(original), ast.dump(restored))

    def test_visitors_and_transformers(self):
        class Names(ast.NodeVisitor):
            def __init__(self):
                self.names = []

            def visit_Name(self, node):
                self.names.append(node.id)

        class Rename(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id == 'x':
                    return ast.copy_location(ast.Name(id='item', ctx=node.ctx), node)
                return node

        tree = ast.parse('(from x in values where x > 0 select x * 2)')
        visitor = Names()
        visitor.visit(tree)
        self.assertEqual(visitor.names, ['x', 'values', 'x', 'x'])
        self.assertEqual([node.id for node in ast.walk(tree) if isinstance(node, ast.Name)].count('x'), 3)
        transformed = Rename().visit(tree)
        expected = ast.parse('(from item in values where item > 0 select item * 2)')
        self.assertEqual(ast.dump(ast.parse(ast.unparse(transformed))), ast.dump(expected))

    def test_postponed_query_annotations(self):
        for query in (
            '(from x in values where x > 0 where x < 10 select x * 2)',
            '(from x in values select (from y in x select y))',
            '(from x in (left if flag else right) select lambda y: x + y)',
        ):
            with self.subTest(query=query):
                source = ('from __future__ import annotations\n'
                          f'def annotated(value: {query}):\n    return value\n')
                namespace = {}
                exec(compile(source, '<annotations>', 'exec'), namespace)
                function = namespace['annotated']
                self.assertEqual(function(42), 42)
                restored = ast.parse(function.__annotations__['value'], mode='eval')
                self.assertEqual(ast.dump(restored), ast.dump(ast.parse(query, mode='eval')))


class QueryRuntimeTests(unittest.TestCase):
    def test_results_preserve_order_and_duplicates(self):
        for values, expected in (([], []), ([3, 1, 3], [6, 2, 6])):
            with self.subTest(values=values):
                query = _query(values, (), lambda x: x * 2, True)
                self.assertEqual(list(query), expected)

    def test_deferred_callbacks_and_short_circuit_order(self):
        events = []

        def positive(x):
            events.append(('positive', x))
            return x > 0

        def even(x):
            events.append(('even', x))
            return x % 2 == 0

        def project(x):
            events.append(('project', x))
            return x * 10

        query = _query([0, 1, 2, 4], (positive, even), project, False)
        self.assertEqual(events, [])
        iterator = iter(query)
        self.assertEqual(events, [])
        self.assertEqual(next(iterator), 20)
        self.assertEqual(events, [('positive', 0), ('positive', 1), ('even', 1),
                                  ('positive', 2), ('even', 2), ('project', 2)])
        self.assertEqual(list(iterator), [40])

    def test_independent_and_repeated_iterations(self):
        query = _query([1, 2, 3], (), lambda x: x * 2, False)
        first, second = iter(query), iter(query)
        self.assertEqual(next(first), 2)
        self.assertEqual(next(second), 2)
        self.assertEqual(list(first), [4, 6])
        self.assertEqual(list(second), [4, 6])
        self.assertEqual(list(query), [2, 4, 6])

    def test_source_retention_and_mutation(self):
        source = [1, 2]
        original = source
        query = _query(source, (), lambda x: x, True)
        source = [99]
        iterator = iter(query)
        self.assertEqual(next(iterator), 1)
        original.append(3)
        self.assertEqual(list(iterator), [2, 3])
        self.assertEqual(list(query), [1, 2, 3])
        original.clear()
        self.assertEqual(list(query), [])

    def test_callbacks_observe_late_bound_values(self):
        limit = 1
        query = _query([1, 2, 3], (lambda x: x > limit,), lambda x: x + limit, False)
        limit = 2
        self.assertEqual(list(query), [5])

    def test_list_subclass_iteration_is_honored(self):
        events = []

        class Source(list):
            def __iter__(self):
                events.append('iter')
                return iter([3, 1])

        query = _query(Source([1, 2]), (), lambda x: x, True)
        self.assertEqual(events, [])
        iterator = iter(query)
        self.assertEqual(events, ['iter'])
        self.assertEqual(list(iterator), [3, 1])

    def test_query_sources_keep_parent_projection(self):
        events = []

        def project(x):
            events.append(x)
            return x + 10

        parent = _query([1, 2], (), project, False)
        child = _query(parent, (lambda x: x > 11,), lambda x: x * 2, False)
        self.assertEqual(events, [])
        self.assertEqual(list(child), [24])
        self.assertEqual(events, [1, 2])
        self.assertEqual(list(parent), [11, 12])

    def test_invalid_factory_inputs_fail_immediately(self):
        events = []

        def callback(x):
            events.append(x)
            return x

        for args in (
            ((), (), callback, False), (iter([]), (), callback, False),
            (None, (), callback, False), ([], [], callback, False),
            ([], (None,), callback, False), ([], (), None, False),
            ([], (), callback, 1), ([], (), callback),
        ):
            with self.subTest(args=args):
                with self.assertRaises(TypeError):
                    _query(*args)
        self.assertEqual(events, [])

    def test_callback_errors_close_only_the_affected_iterator(self):
        calls = 0
        failure = ValueError('callback failed')

        def predicate(x):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise failure
            return True

        query = _query([1, 2], (predicate,), lambda x: x, True)
        failed, other = iter(query), iter(query)
        with self.assertRaises(ValueError) as raised:
            next(failed)
        self.assertIs(raised.exception, failure)
        self.assertEqual(next(failed, 'done'), 'done')
        self.assertEqual(list(other), [1, 2])
        self.assertEqual(list(query), [1, 2])

    def test_predicate_truth_and_selector_exceptions(self):
        for exception_type in (ValueError, StopIteration):
            for phase in ('predicate', 'truth', 'selector'):
                with self.subTest(exception=exception_type, phase=phase):
                    failure = exception_type('failed')

                    def fail(x=None):
                        raise failure

                    class Truth:
                        def __bool__(self):
                            return fail()

                    predicate = fail if phase == 'predicate' else lambda x: Truth()
                    predicates = () if phase == 'selector' else (predicate,)
                    selector = fail if phase == 'selector' else lambda x: x
                    iterator = iter(_query([1], predicates, selector, False))
                    expected = RuntimeError if exception_type is StopIteration else ValueError
                    with self.assertRaises(expected) as raised:
                        next(iterator)
                    if exception_type is StopIteration:
                        self.assertIs(raised.exception.__cause__, failure)
                    else:
                        self.assertIs(raised.exception, failure)
                    self.assertEqual(next(iterator, 'done'), 'done')

    def test_source_errors_and_exhaustion(self):
        failure = LookupError('source failed')

        class Source(list):
            def __iter__(self):
                return self

            def __next__(self):
                raise failure

        iterator = iter(_query(Source(), (), lambda x: x, True))
        with self.assertRaises(LookupError) as raised:
            next(iterator)
        self.assertIs(raised.exception, failure)
        self.assertEqual(next(iterator, 'done'), 'done')
        self.assertEqual(list(_query([], (), lambda x: x, True)), [])

        class BadSource(list):
            def __iter__(self):
                raise failure

        query = _query(BadSource(), (), lambda x: x, True)
        with self.assertRaises(LookupError):
            iter(query)

    def test_ownership_until_exhaustion(self):
        class Source(list):
            pass

        class Callback:
            def __call__(self, x):
                return x

        source, callback = Source([1]), Callback()
        source_ref, callback_ref = weakref.ref(source), weakref.ref(callback)
        query = _query(source, (), callback, True)
        iterator = iter(query)
        del source, callback, query
        gc.collect()
        self.assertIsNotNone(source_ref())
        self.assertIsNotNone(callback_ref())
        self.assertEqual(list(iterator), [1])
        gc.collect()
        self.assertIsNone(source_ref())
        self.assertIsNone(callback_ref())

    def test_query_and_iterator_cycles_are_collected(self):
        class Source(list):
            pass

        class Callback:
            def __call__(self, x):
                return x

        for cycle in ('query', 'iterator'):
            with self.subTest(cycle=cycle):
                source, callback = Source([1]), Callback()
                source_ref, callback_ref = weakref.ref(source), weakref.ref(callback)
                query = _query(source, (), callback, True)
                iterator = iter(query)
                if cycle == 'query':
                    source.append(query)
                else:
                    callback.iterator = iterator
                del source, callback, query, iterator
                gc.collect()
                self.assertIsNone(source_ref())
                self.assertIsNone(callback_ref())

    def test_native_query_is_immutable(self):
        query = _query([], (), lambda x: x, True)
        for kind in (type(query), type(iter(query))):
            with self.subTest(kind=kind):
                with self.assertRaises(TypeError):
                    kind()
                with self.assertRaises(TypeError):
                    type('DerivedQuery', (kind,), {})
                with self.assertRaises(TypeError):
                    kind.attribute = 1
        with self.assertRaises(AttributeError):
            query.source = []

    def test_reentrant_advancement_is_rejected(self):
        iterator = None

        def project(x):
            return next(iterator)

        iterator = iter(_query([1, 2], (), project, False))
        with self.assertRaises(RuntimeError):
            next(iterator)
        self.assertEqual(next(iterator, 'done'), 'done')

    def test_native_runtime_program_output(self):
        result = subprocess.run([sys.executable, '-c', '''
from _linq import _query
query = _query([3, 1, 2, 3], (lambda x: x > 1,), lambda x: x * 2, False)
print(list(query))
'''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '[6, 4, 6]\n')

    def test_subinterpreter_state_is_independent(self):
        import _linq
        from test import support

        query = _query([1], (), lambda x: x, True)
        _linq._test_marker = True
        self.addCleanup(delattr, _linq, '_test_marker')
        code = f'''
import _linq
assert not hasattr(_linq, '_test_marker')
query = _linq._query([1, 2], (lambda x: x > 1,), lambda x: x * 2, False)
assert id(type(query)) != {id(type(query))}
assert list(query) == [4]
'''
        result = support.run_in_subinterp_with_config(
            code, own_gil=True, use_main_obmalloc=False,
            allow_fork=False, allow_exec=False, allow_threads=True,
            allow_daemon_threads=False, check_multi_interp_extensions=True,
        )
        self.assertEqual(result, 0)
        self.assertEqual(list(query), [1])

    def test_deep_composition_does_not_crash(self):
        result = subprocess.run([sys.executable, '-c', '''
import gc
from _linq import _query
identity = lambda x: x
query = _query([1], (lambda x: True,) * 20000, identity, True)
iterator = iter(query)
try:
    assert list(iterator) == [1]
except RecursionError:
    pass
del query, iterator
gc.collect()
query = [1]
for _ in range(20000):
    query = _query(query, (), identity, True)
try:
    assert list(query) == [1]
except RecursionError:
    pass
del query
gc.collect()
print('ok')
'''], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'ok\n')


if __name__ == '__main__':
    unittest.main()
