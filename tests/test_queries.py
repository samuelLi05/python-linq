"""Functional checks for the current query frontend and built interpreter."""

import ast
import subprocess
import symtable
import sys
import unittest


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


if __name__ == '__main__':
    unittest.main()
