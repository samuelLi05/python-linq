"""Functional checks for the current query frontend and built interpreter."""

import ast
import subprocess
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


if __name__ == '__main__':
    unittest.main()
