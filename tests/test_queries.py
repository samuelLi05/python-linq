"""Functional checks for native queries, compiler inspection, and the C runtime."""

import ast
import dis
import json
import os
from pathlib import Path
import gc
import subprocess
import symtable
import sys
import tempfile
import textwrap
import unittest
import weakref

from _linq import _inspect, _query


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

    def test_native_query_program_executes(self):
        result = subprocess.run(
            [sys.executable, '-c', '''
print("started")
query = (from x in [1, 2, 3] where x > 1 select x * 2)
print(list(query))
'''], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'started\n[4, 6]\n')
        self.assertEqual(result.stderr, '')

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


class QueryExecutionTests(unittest.TestCase):
    def test_values_and_expression_positions(self):
        for values in ([], [3, 1, 2, 3]):
            with self.subTest(values=values):
                query = (from x in values where x > 1 where x < 4 select x * 2)
                self.assertEqual(list(query), [x * 2 for x in values if 1 < x < 4])
                self.assertEqual(list((from x in values select x)), values)

        def make(values):
            return (from x in values select x + 1)

        self.assertEqual(list(make([1, 2])), [2, 3])
        self.assertEqual(list((from y in (from x in [1, 2] select x * 2)
                               where y > 2 select y + 1)), [5])
        nested = (from x in [[1, 2], [3]] select (from y in x select y * 2))
        self.assertEqual([list(row) for row in nested], [[2, 4], [6]])

    def test_construction_and_callback_timing(self):
        events = []

        def source():
            events.append('source')
            return [0, 1, 2]

        def predicate(x, name):
            events.append((name, x))
            return x > 0 if name == 'first' else x > 1

        def project(x):
            events.append(('project', x))
            return x * 2

        query = (from x in source() where predicate(x, 'first')
                 where predicate(x, 'second') select project(x))
        self.assertEqual(events, ['source'])
        iterator = iter(query)
        self.assertEqual(events, ['source'])
        self.assertEqual(next(iterator), 4)
        self.assertEqual(events, ['source', ('first', 0), ('first', 1), ('second', 1),
                                  ('first', 2), ('second', 2), ('project', 2)])

    def test_retained_source_and_independent_iterations(self):
        values = [1, 2]
        original = values
        query = (from values in values select values * 2)
        values = [9]
        original.append(3)
        first, second = iter(query), iter(query)
        self.assertEqual(next(first), 2)
        self.assertEqual(list(second), [2, 4, 6])
        self.assertEqual(list(first), [4, 6])
        self.assertEqual(list(query), [2, 4, 6])
        self.assertEqual(values, [9])

    def test_captured_cells_and_target_isolation(self):
        def make():
            limit = 1

            def change(value):
                nonlocal limit
                limit = value

            query = (from x in [1, 2, 3] where x > limit select x + limit)
            self.assertNotIn('x', locals())
            return query, change

        query, change = make()
        self.assertEqual(list(query), [3, 4])
        change(2)
        self.assertEqual(list(query), [5])

    def test_clause_root_scopes_and_nested_captures(self):
        functions = list((from x in [1, 2] select lambda z: x + z))
        self.assertEqual([function(10) for function in functions], [11, 12])
        self.assertEqual(list((from x in [1, 2] select [x + z for z in [10, 20]])),
                         [[11, 21], [12, 22]])
        self.assertEqual(list((from x in [1, 2] select {x + z for z in [10]})),
                         [{11}, {12}])
        self.assertEqual(list((from x in [1, 2] select {z: x + z for z in [10]})),
                         [{10: 11}, {10: 12}])
        generators = list((from x in [1, 2] select (x + z for z in [10, 20])))
        self.assertEqual([list(generator) for generator in generators], [[11, 21], [12, 22]])
        self.assertEqual(list((from x in [1, 2] where (lambda z: z + x)(0) > 1
                               select x)), [2])
        self.assertEqual([list((from y in [x] select x + y)) for x in [1, 2]],
                         [[2], [4]])

    def test_globals_class_context_and_private_names(self):
        namespace = {}
        exec('''
limit = 1
def make(values):
    global limit
    return (from x in values where x > limit select x)
class Queries:
    values = [1, 2, 3]
    limit = 99
    query = (from x in values where x > limit select x)
    private = (from __x in values select _Queries__x)
''', namespace)
        query = namespace['make']([1, 2, 3])
        self.assertEqual(list(query), [2, 3])
        cls = namespace['Queries']
        self.assertEqual(list(cls.query), [2, 3])
        self.assertEqual(list(cls.private), [1, 2, 3])
        namespace['limit'] = 2
        self.assertEqual(list(query), [3])
        self.assertEqual(list(cls.query), [3])

    def test_list_subclass_and_failures(self):
        events = []

        class Source(list):
            def __iter__(self):
                events.append('iter')
                return iter([3, 2])

        query = (from x in Source() select x * 2)
        self.assertEqual(events, [])
        self.assertEqual(list(query), [6, 4])
        self.assertEqual(events, ['iter'])
        with self.assertRaises(TypeError):
            query = (from x in (1, 2) select x)

        def fail(x):
            raise ValueError('callback')

        query = (from x in [1] select fail(x))
        iterator = iter(query)
        with self.assertRaisesRegex(ValueError, 'callback'):
            next(iterator)
        self.assertEqual(list(iterator), [])

    def test_chained_queries_keep_parent_projection(self):
        events = []

        def project(x):
            events.append(x)
            return x + 10

        parent = (from x in [1, 2] select project(x))
        child = (from x in parent where x > 11 select x * 2)
        self.assertEqual(events, [])
        self.assertEqual(list(child), [24])
        self.assertEqual(events, [1, 2])

    def test_ast_compilation_and_compile_modes(self):
        tree = ast.parse('(from x in [1, 2] select x + 1)', mode='eval')
        self.assertEqual(list(eval(compile(tree, '<ast>', 'eval'))), [2, 3])
        for optimize in (0, 1, 2):
            code = compile('(from x in [1, 2] select x)', '<eval>', 'eval', optimize=optimize)
            self.assertEqual(list(eval(code)), [1, 2])
        namespace = {}
        exec(compile('q = (from x in [1] select x)', '<single>', 'single'), namespace)
        self.assertEqual(list(namespace['q']), [1])
        optimized = compile(tree, '<ast>', 'eval', ast.PyCF_OPTIMIZED_AST)
        self.assertIsInstance(optimized.body, ast.QueryExpr)

        class Increment(ast.NodeTransformer):
            def visit_Constant(self, node):
                return ast.copy_location(ast.Constant(node.value + 1), node)

        transformed = Increment().visit(ast.parse('(from x in [1, 2] select x)', mode='eval'))
        self.assertEqual(list(eval(compile(transformed, '<transformed>', 'eval'))), [2, 3])

    def test_annotations_aliases_and_ordinary_control_flow(self):
        namespace = {}
        exec('''
values = [1, 2]
def annotated(value: (from x in values select x * 2)):
    return value
type Alias = (from x in values select x + 1)
def generate():
    for value in values:
        yield (from x in [value] select x * 2)
async def asynchronous():
    return (from x in values select x)
''', namespace)
        self.assertEqual(list(namespace['annotated'].__annotations__['value']), [2, 4])
        self.assertEqual(list(namespace['Alias'].__value__), [2, 3])
        self.assertEqual([list(q) for q in namespace['generate']()], [[2], [4]])
        import asyncio
        self.assertEqual(list(asyncio.run(namespace['asynchronous']())), [1, 2])

    def test_helper_names_do_not_enter_user_namespaces(self):
        namespace = {'_query': object(), '_linq': object(), '__builtins__': __builtins__}
        exec('q = (from x in [1] select x)', namespace)
        self.assertEqual(set(namespace), {'_query', '_linq', '__builtins__', 'q'})
        self.assertEqual(list(namespace['q']), [1])

    def test_large_predicate_sequence(self):
        source = '(from x in [1, 2] ' + 'where x > 0 ' * 100 + 'select x)'
        self.assertEqual(list(eval(source)), [1, 2])


class QueryInspectionTests(unittest.TestCase):
    def test_ordered_ir_and_binding_metadata(self):
        tree = ast.parse('''
def make(values, limit):
    return (from x in values where x > limit where x < 10 select x)
''')
        info = _inspect(tree)
        query, = info['queries']
        self.assertEqual(query['mode'], 'normal')
        source, first, second, project = query['operators']
        self.assertEqual([op['kind'] for op in query['operators']],
                         ['Source', 'Filter', 'Filter', 'Project'])
        self.assertEqual(source['binding'], {'name': 'values', 'scope': 'local'})
        self.assertEqual(source['input'], 'none')
        self.assertTrue(all(op['result'] == 'sequence' for op in query['operators']))
        self.assertEqual(first['input'], 'sequence')
        self.assertEqual(first['captures'], ['limit'])
        self.assertEqual(second['captures'], [])
        self.assertEqual(project['parameter'], 'x')
        self.assertTrue(project['identity'])
        self.assertNotEqual(first['scope']['id'], second['scope']['id'])
        self.assertEqual(info, _inspect(tree))

    def test_inspection_does_not_execute_and_preserves_ast(self):
        tree = ast.parse('''
raise RuntimeError('must not execute')
import module_that_does_not_exist
@decorator_that_does_not_exist
def f(value=(from x in source_that_does_not_exist() select callback(x))):
    return value
''')
        before = ast.dump(tree, include_attributes=True)
        info = _inspect(tree)
        self.assertEqual(len(info['queries']), 1)
        self.assertEqual(ast.dump(tree, include_attributes=True), before)
        self.assertFalse(info['queries'][0]['operators'][-1]['identity'])

    def test_nested_inputs_and_mangled_identity(self):
        info = _inspect(ast.parse('(from y in (from x in values select x) select y + 1)'))
        outer, inner = info['queries']
        self.assertEqual(outer['operators'][0]['input_plan'], inner['id'])
        self.assertFalse(outer['operators'][-1]['identity'])
        info = _inspect(ast.parse('class C:\n    q = (from __x in values select _C__x)'))
        self.assertTrue(info['queries'][0]['operators'][-1]['identity'])

    def test_inspection_modes_and_invalid_inputs(self):
        for mode in ('exec', 'eval', 'single'):
            with self.subTest(mode=mode):
                info = _inspect(ast.parse('(from x in values select x)', mode=mode), mode=mode)
                self.assertEqual(len(info['queries']), 1)
        for mode in ('func_type', 'unknown'):
            with self.assertRaises(ValueError):
                _inspect(ast.parse('1'), mode=mode)
        with self.assertRaises(TypeError):
            _inspect('source')
        with self.assertRaises(TypeError):
            _inspect(ast.parse('1'), mode='eval')
        for level in (-2, 3):
            with self.assertRaises(ValueError):
                _inspect(ast.parse('1'), optimize=level)
        tree = ast.parse('(from x in values select x)')
        tree.body[0].value.target.ctx = ast.Load()
        with self.assertRaises(ValueError):
            _inspect(tree)

    def test_clause_code_objects_and_factory_call(self):
        import _testinternalcapi

        code = compile('''
def make(values, limit):
    return (from x in values where x > limit select x * 2)
''', '<codegen>', 'exec')
        function, = [item for item in code.co_consts if isinstance(item, type(code))]
        clauses = [item for item in function.co_consts if isinstance(item, type(code))]
        self.assertEqual([clause.co_name for clause in clauses], ['linq_where', 'linq_select'])
        self.assertEqual(clauses[0].co_freevars, ('limit',))
        self.assertEqual(clauses[0].co_varnames[:1], ('x',))
        operations = list(dis.get_instructions(function))
        self.assertTrue(any(op.opname == 'IMPORT_NAME' and op.argval == '_linq'
                            for op in operations))
        self.assertTrue(any(op.opname == 'IMPORT_FROM' and op.argval == '_query'
                            for op in operations))
        self.assertTrue(any(op.opname == 'CALL' and op.arg == 4 for op in operations))
        self.assertNotIn('x', function.co_varnames)
        self.assertNotIn('_query', function.co_varnames)
        sequence, _ = _testinternalcapi.compiler_codegen(
            ast.parse('q = (from x in [1] where x > 0 select x)'), '<instructions>', 0)
        self.assertTrue(sequence.get_instructions())

    def test_compilation_and_inspection_release_ast(self):
        tree = ast.parse('q = (from x in [1] select lambda z: x + z)')
        reference = weakref.ref(tree)
        code = compile(tree, '<ownership>', 'exec')
        _inspect(tree)
        del tree
        gc.collect()
        self.assertIsNone(reference())
        namespace = {}
        exec(code, namespace)
        self.assertEqual(next(iter(namespace['q']))(2), 3)
        for source in ('return (from x in [1] select x)',
                       '(from x in [1] select f(repeated=1, repeated=2))'):
            with self.assertRaises(SyntaxError):
                compile(source, '<failure>', 'exec')
        self.assertEqual(list(eval('(from x in [2] select x)')), [2])

    def test_driver_ir_disassembly_and_execution(self):
        driver = Path(os.environ.get('LINQ_DRIVER',
                      Path(__file__).resolve().parents[1] / 'scripts' / 'linq.py'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'program.py'
            (Path(directory) / 'sibling.py').write_text('value = 7\n')
            path.write_bytes(b'# coding: latin-1\nimport sibling, sys\n'
                             b'q = (from x in [1, 2] select x + sibling.value)\n'
                             b'print("caf\xe9", list(q), sys.argv[1:])\n')
            result = subprocess.run([sys.executable, str(driver), 'ir', str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)['queries']), 1)
            result = subprocess.run([sys.executable, str(driver), 'dis', str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('linq_select', result.stdout)
            result = subprocess.run([sys.executable, str(driver), 'run', '--optimize', '1',
                                     str(path), 'argument'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "café [8, 9] ['argument']\n")


class QueryAnalysisTests(unittest.TestCase):
    producer = 'p = (from x in values where x > 0 select x)'
    consumer = 'q = (from y in p where y < 10 select y)'

    def inspect_body(self, body):
        source = 'def example(values, flag=True):\n' + textwrap.indent(textwrap.dedent(body), '    ')
        return _inspect(ast.parse(source)), source

    def pair(self, before='', between='', after='return list(q)', producer=None, consumer=None):
        body = '\n'.join(part for part in (before, producer or self.producer, between,
                         consumer or self.consumer, after) if part)
        info, source = self.inspect_body(body)
        return info, info['analysis']['candidates'][-1], source

    def test_adjacent_pair_is_eligible_and_execution_stays_normal(self):
        info, decision, source = self.pair()
        self.assertEqual(decision['status'], 'eligible')
        self.assertEqual(decision['reason'], 'eligible')
        self.assertEqual(decision['producer_plan'], 0)
        self.assertTrue(decision['dominates'])
        self.assertTrue(decision['adjacent'])
        self.assertEqual(decision['reaching_definitions'], [decision['producer_definition']])
        self.assertTrue(all(query['mode'] == 'normal' for query in info['queries']))
        namespace = {}
        exec(compile(source, '<analysis>', 'exec'), namespace)
        self.assertEqual(namespace['example']([-1, 1, 3, 20]), [1, 3])
        instructions = list(dis.get_instructions(namespace['example']))
        self.assertEqual([op.argval for op in instructions if op.opname == 'IMPORT_FROM'],
                         ['_query', '_query'])

    def test_multiple_filters_and_identity_only_producer(self):
        for producer in (self.producer, 'p = (from x in values select x)'):
            with self.subTest(producer=producer):
                _, decision, _ = self.pair(producer=producer,
                    consumer='q = (from y in p where y > 1 where y < 10 select y)')
                self.assertEqual(decision['status'], 'eligible')

    def test_query_range_variable_can_shadow_source_binding(self):
        _, decision, _ = self.pair(consumer='q = (from p in p where p < 10 select p)')
        self.assertEqual(decision['status'], 'eligible')

    def test_branch_pair_dominates_within_its_path(self):
        info, _ = self.inspect_body('if flag:\n' + textwrap.indent(
            self.producer + '\n' + self.consumer + '\nreturn list(q)', '    ') + '\nreturn []')
        decision = info['analysis']['candidates'][-1]
        self.assertEqual(decision['status'], 'eligible')
        self.assertTrue(decision['dominates'])
        function, = info['analysis']['functions']
        blocks = function['blocks']
        self.assertEqual(len(blocks[0]['successors']), 2)
        source_use = function['uses'][decision['source_use']]
        definition = function['definitions'][decision['producer_definition']]
        self.assertIn(definition['block'], blocks[source_use['block']]['dominators'])

    def test_branch_join_preserves_ambiguous_definitions(self):
        info, _ = self.inspect_body('if flag:\n    ' + self.producer +
            '\nelse:\n    p = (from x in values where x < 0 select x)\n' + self.consumer)
        decision = info['analysis']['candidates'][-1]
        self.assertEqual(decision['reason'], 'ambiguous_producer')
        function, = info['analysis']['functions']
        reaching = decision['reaching_definitions']
        self.assertEqual(len(reaching), 2)
        self.assertEqual({function['definitions'][i]['query'] for i in reaching}, {0, 1})
        use = function['uses'][decision['source_use']]
        self.assertTrue(set(reaching).issubset(function['blocks'][use['block']]['in']))

    def test_nested_branch_join_reaches_a_fixed_point(self):
        info, _ = self.inspect_body('if flag:\n    if values:\n        ' + self.producer +
            '\n    else:\n        p = (from x in values where x < 0 select x)\n'
            'else:\n    p = (from x in values select x)\n' + self.consumer)
        decision = info['analysis']['candidates'][-1]
        self.assertEqual(decision['reason'], 'ambiguous_producer')
        self.assertEqual(len(decision['reaching_definitions']), 3)
        function, = info['analysis']['functions']
        self.assertEqual({function['definitions'][i]['query']
                          for i in decision['reaching_definitions']}, {0, 1, 2})

    def test_returning_branch_is_not_a_join_predecessor(self):
        info, _ = self.inspect_body('if flag:\n    return []\nelse:\n    ' +
                                    self.producer + '\n' + self.consumer)
        decision = info['analysis']['candidates'][-1]
        self.assertEqual(len(decision['reaching_definitions']), 1)
        self.assertTrue(decision['dominates'])
        self.assertEqual(decision['reason'], 'not_adjacent')

    def test_unbound_path_is_not_a_unique_producer(self):
        info, _ = self.inspect_body('if flag:\n    ' + self.producer + '\n' + self.consumer)
        decision = info['analysis']['candidates'][-1]
        self.assertEqual(decision['reason'], 'possibly_unbound')
        function, = info['analysis']['functions']
        self.assertIn('unbound', [function['definitions'][i]['kind']
                                 for i in decision['reaching_definitions']])

    def test_gen_kill_and_reads_before_assignment(self):
        info, decision, _ = self.pair(before='p = 0\nconsume(p)', after='p = 99\nreturn list(q)')
        self.assertEqual(decision['status'], 'eligible')
        function, = info['analysis']['functions']
        p = next(binding for binding in function['bindings'] if binding['name'] == 'p')
        definitions = [function['definitions'][i] for i in p['definitions']]
        latest = definitions[-1]['id']
        block = function['blocks'][0]
        self.assertIn(latest, block['gen'])
        self.assertFalse(set(block['gen']) & set(block['kill']))
        self.assertIn(decision['producer_definition'], block['kill'])
        self.assertNotIn(decision['producer_definition'], block['out'])
        info, decision, _ = self.pair(between='p = p')
        self.assertEqual(decision['reason'], 'not_query_definition')
        function, = info['analysis']['functions']
        alias = next(use for use in function['uses'] if use['kind'] == 'alias')
        reaching, = alias['reaching_definitions']
        self.assertEqual(function['definitions'][reaching]['query'], 0)

    def test_reassigned_query_uses_latest_definition(self):
        info, decision, _ = self.pair(before=self.producer)
        self.assertEqual(decision['status'], 'eligible')
        self.assertEqual(decision['producer_plan'], 1)
        self.assertEqual(len(decision['reaching_definitions']), 1)

    def test_aliases_and_escapes_are_extra_uses(self):
        for extra, reason in (('alias = p', 'producer_aliased'),
                              ('consume(p)', 'producer_extra_use'),
                              ('return p, q', 'producer_extra_use')):
            with self.subTest(extra=extra):
                _, decision, _ = self.pair(after=extra)
                self.assertEqual(decision['status'], 'ineligible')
                self.assertEqual(decision['reason'], reason)
                self.assertIsNotNone(decision['extra_use'])

    def test_shared_producer_is_not_eligible(self):
        _, decision, _ = self.pair(after='r = (from z in p where z != 0 select z)')
        self.assertEqual(decision['reason'], 'producer_extra_use')

    def test_captures_are_separate_from_eager_reads(self):
        info, decision, _ = self.pair(after='capture = lambda: p\nreturn list(q)')
        self.assertEqual(decision['reason'], 'producer_captured')
        function, = info['analysis']['functions']
        self.assertTrue(next(b for b in function['bindings'] if b['name'] == 'p')['captured'])
        info, decision, _ = self.pair(before='limit = 5',
            consumer='q = (from y in p where y < limit select y)')
        self.assertEqual(decision['status'], 'eligible')
        function, = info['analysis']['functions']
        limit = next(b['id'] for b in function['bindings'] if b['name'] == 'limit')
        self.assertFalse(any(use['binding'] == limit for use in function['uses']))

    def test_binding_shadowing_does_not_count_as_capture(self):
        _, decision, _ = self.pair(after='identity = lambda p: p\nreturn list(q)')
        self.assertEqual(decision['status'], 'eligible')

    def test_projection_filter_adjacency_and_target_requirements(self):
        cases = (
            {'producer': 'p = (from x in values select x + 1)', 'reason': 'producer_projection'},
            {'consumer': 'q = (from y in p where y < 10 select y * 2)', 'reason': 'consumer_projection'},
            {'consumer': 'q = (from y in p select y)', 'reason': 'consumer_without_filter'},
            {'between': 'pass', 'reason': 'not_adjacent'},
            {'consumer': 'p = (from y in p where y < 10 select y)', 'after': '', 'reason': 'same_binding'},
        )
        for options in cases:
            options = dict(options)
            reason = options.pop('reason')
            with self.subTest(reason=reason):
                _, decision, _ = self.pair(**options)
                self.assertEqual(decision['status'], 'ineligible')
                self.assertEqual(decision['reason'], reason)

    def test_unique_dominating_definition_can_still_fail_adjacency(self):
        info, _ = self.inspect_body(self.producer + '\nif flag:\n    ' + self.consumer)
        decision = info['analysis']['candidates'][-1]
        self.assertEqual(decision['reason'], 'not_adjacent')
        self.assertTrue(decision['dominates'])
        self.assertFalse(decision['adjacent'])

    def test_unsupported_construct_makes_whole_function_unknown(self):
        for statement in ('for ignored in []:\n    pass', 'while flag:\n    break',
                          'try:\n    pass\nexcept Exception:\n    pass',
                          'with context():\n    pass', 'import math',
                          'def nested():\n    pass', 'a, b = values',
                          'temporary = [x for x in values]', 'temporary = (a := 1)'):
            with self.subTest(statement=statement):
                info, decision, _ = self.pair(after='return list(q)\n' + statement)
                self.assertEqual(decision['status'], 'unknown')
                function, = info['analysis']['functions']
                self.assertEqual(function['status'], 'unknown')
                self.assertTrue(function['reasons'])
                self.assertNotIn('blocks', function)
                self.assertNotIn('definitions', function)

    def test_dynamic_namespace_and_frame_access_are_unknown(self):
        for expression in ('locals()', 'globals()', 'vars()', 'dir()', 'exec("pass")',
                           'eval("1")', 'alias = locals', 'sys._getframe()',
                           'inspect.currentframe()', 'frame.f_locals',
                           'callback = lambda: (temporary := locals())'):
            with self.subTest(expression=expression):
                _, decision, _ = self.pair(after=expression)
                self.assertEqual(decision['status'], 'unknown')

    def test_nested_function_analysis_is_independent(self):
        info = _inspect(ast.parse("""
def outer(values):
    p = (from x in values select x)
    def inner(values):
        p = (from x in values where x > 0 select x)
        q = (from y in p where y < 10 select y)
        return list(q)
    q = (from y in p where y < 10 select y)
    return inner
"""))
        functions = {function['name']: function for function in info['analysis']['functions']}
        self.assertEqual(functions['outer']['status'], 'unknown')
        self.assertEqual(functions['inner']['status'], 'analyzed')
        decisions = info['analysis']['candidates']
        self.assertEqual([item['status'] for item in decisions],
                         ['unknown', 'ineligible', 'eligible', 'unknown'])

    def test_async_generators_and_other_scopes_are_unknown(self):
        for prefix, tail in (('async def f(values):', 'return q'),
                             ('def f(values):', 'yield q')):
            source = prefix + '\n' + textwrap.indent(self.producer + '\n' + self.consumer + '\n' + tail, '    ')
            info = _inspect(ast.parse(source))
            self.assertEqual(info['analysis']['candidates'][-1]['status'], 'unknown')
        for source in (self.producer + '\n' + self.consumer,
                       'class C:\n' + textwrap.indent(self.producer + '\n' + self.consumer, '    '),
                       'f = lambda values: (from x in values select x)'):
            self.assertTrue(all(item['reason'] == 'unsupported_scope'
                                for item in _inspect(ast.parse(source))['analysis']['candidates']))

    def test_global_nonlocal_and_class_mangled_bindings(self):
        info, _ = self.inspect_body('global p\n' + self.consumer)
        self.assertEqual(info['analysis']['candidates'][-1]['reason'], 'nonlocal_source_binding')
        source = 'def outer(p):\n    def inner(values):\n        nonlocal p\n        ' + self.consumer
        info = _inspect(ast.parse(source))
        self.assertEqual(info['analysis']['candidates'][-1]['reason'], 'nonlocal_source_binding')
        source = 'class C:\n    def f(self, values):\n        __p = (from __x in values select _C__x)\n        q = (from y in __p where y > 0 select y)'
        info = _inspect(ast.parse(source))
        self.assertEqual(info['analysis']['candidates'][-1]['status'], 'eligible')

    def test_reports_are_deterministic_and_do_not_execute_source(self):
        _, _, source = self.pair(before='missing_function()', after='return missing_result(q)')
        tree = ast.parse(source)
        before = ast.dump(tree, include_attributes=True)
        first = _inspect(tree)
        self.assertEqual(first, _inspect(tree))
        self.assertEqual(ast.dump(tree, include_attributes=True), before)
        self.assertEqual(first['analysis']['candidates'][-1]['status'], 'eligible')
        for level in (0, 1, 2):
            self.assertEqual(_inspect(tree, optimize=level)['analysis']['candidates'][-1]['status'], 'eligible')

    def test_unreachable_queries_are_not_eligible(self):
        info, _ = self.inspect_body('return []\n' + self.producer + '\n' + self.consumer)
        self.assertEqual(info['analysis']['candidates'][-1]['reason'], 'unreachable')
        function, = info['analysis']['functions']
        unreachable = [block for block in function['blocks'] if not block['reachable']]
        self.assertTrue(unreachable)
        self.assertTrue(all(not b['in'] and not b['out'] and not b['dominators'] for b in unreachable))

    def test_large_cfg_and_definition_sets(self):
        before = '\n'.join('a' + str(i) + ' = ' + str(i) for i in range(150))
        branches = '\n'.join('if flag:\n    pass\nelse:\n    pass' for _ in range(30))
        info, decision, _ = self.pair(before=before + '\n' + branches)
        self.assertEqual(decision['status'], 'eligible')
        function, = info['analysis']['functions']
        self.assertGreater(len(function['definitions']), 256)
        self.assertGreater(len(function['blocks']), 64)

    def test_analysis_driver_reports_without_execution(self):
        driver = Path(os.environ.get('LINQ_DRIVER',
                      Path(__file__).resolve().parents[1] / 'scripts' / 'linq.py'))
        _, _, source = self.pair()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'analysis.py'
            path.write_text('raise RuntimeError("must not execute")\n' + source)
            result = subprocess.run([sys.executable, str(driver), 'analyze', str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report['candidates'][-1]['status'], 'eligible')
            self.assertEqual(report['functions'][0]['status'], 'analyzed')


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
assert list((from x in [1, 2] where x > 1 select x * 2)) == [4]
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
