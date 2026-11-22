#include "Python.h"
#include "_linq/linq_runtime.h"
/* BEGIN LINQ override: compiler inspection. */
#include "pycore_compile.h"
/* END LINQ override */

typedef struct {
    PyObject_HEAD
    PyObject *source;
    LinqOperator *operators;
    Py_ssize_t operator_count;
    int identity_projection;
} LinqQuery;

static int
query_traverse(PyObject *object, visitproc visit, void *arg)
{
    LinqQuery *query = (LinqQuery *)object;
    Py_VISIT(Py_TYPE(query));
    Py_VISIT(query->source);
    for (Py_ssize_t i = 0; i < query->operator_count; i++) {
        Py_VISIT(query->operators[i].config);
    }
    return 0;
}

static int
query_clear(PyObject *object)
{
    LinqQuery *query = (LinqQuery *)object;
    Py_CLEAR(query->source);
    for (Py_ssize_t i = 0; i < query->operator_count; i++) {
        Py_CLEAR(query->operators[i].config);
    }
    return 0;
}

static void
query_dealloc(PyObject *object)
{
    LinqQuery *query = (LinqQuery *)object;
    PyTypeObject *type = Py_TYPE(object);
    PyObject_GC_UnTrack(object);
    query_clear(object);
    PyMem_Free(query->operators);
    type->tp_free(object);
    Py_DECREF(type);
}

static PyObject *
query_iter_impl(PyObject *object)
{
    LinqQuery *query = (LinqQuery *)object;
    if (query->source == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "LINQ query has been cleared");
        return NULL;
    }
    LinqState *state = PyType_GetModuleState(Py_TYPE(object));
    if (state == NULL) {
        return NULL;
    }
    PyObject *upstream = PyObject_GetIter(query->source);
    if (upstream == NULL) {
        return NULL;
    }
    for (Py_ssize_t i = 0; i < query->operator_count; i++) {
        PyObject *stage = LinqOperator_Iter(state, &query->operators[i], upstream);
        Py_DECREF(upstream);
        if (stage == NULL) {
            return NULL;
        }
        upstream = stage;
    }
    LINQ_COUNT(state, LINQ_QUERY_ITERATORS);
    return upstream;
}

static PyObject *
query_iter(PyObject *object)
{
    if (Py_EnterRecursiveCall(" while creating a LINQ iterator")) {
        return NULL;
    }
    PyObject *result = query_iter_impl(object);
    Py_LeaveRecursiveCall();
    return result;
}

static PyType_Slot query_slots[] = {
    {Py_tp_dealloc, query_dealloc},
    {Py_tp_traverse, query_traverse},
    {Py_tp_clear, query_clear},
    {Py_tp_iter, query_iter},
    {0, NULL}
};

static PyType_Spec query_spec = {
    .name = "_linq.Query",
    .basicsize = sizeof(LinqQuery),
    .flags = (Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC |
              Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION),
    .slots = query_slots,
};

static int
query_arguments(LinqState *state, PyObject *args, int fused,
                PyObject **source, PyObject **predicates,
                PyObject **selector, PyObject **identity)
{
    if (!PyArg_ParseTuple(args, fused ? "OOOO:_fused_query" : "OOOO:_query",
                         source, predicates, selector, identity)) {
        return -1;
    }
    if (!PyList_Check(*source) && Py_TYPE(*source) != state->query_type) {
        PyErr_SetString(PyExc_TypeError, "LINQ source must be a list or native query");
        return -1;
    }
    if (!PyTuple_Check(*predicates)) {
        PyErr_SetString(PyExc_TypeError, "LINQ predicates must be a tuple of callables");
        return -1;
    }
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(*predicates); i++) {
        if (!PyCallable_Check(PyTuple_GET_ITEM(*predicates, i))) {
            PyErr_SetString(PyExc_TypeError, "LINQ predicate must be callable");
            return -1;
        }
    }
    if (!PyCallable_Check(*selector)) {
        PyErr_SetString(PyExc_TypeError, "LINQ selector must be callable");
        return -1;
    }
    if (!PyBool_Check(*identity)) {
        PyErr_SetString(PyExc_TypeError, "LINQ identity metadata must be a bool");
        return -1;
    }
    return 0;
}

static PyObject *
query_create(LinqState *state, PyObject *source, PyObject *predicates,
             PyObject *selector, PyObject *identity, LinqQuery *producer)
{
    Py_ssize_t prefix = producer ? producer->operator_count - 1 : 0;
    Py_ssize_t count = PyTuple_GET_SIZE(predicates);
    Py_ssize_t maximum = PY_SSIZE_T_MAX / (Py_ssize_t)sizeof(LinqOperator);
    if (prefix > maximum - 1 || count > maximum - 1 - prefix) {
        return PyErr_NoMemory();
    }
    Py_ssize_t total = prefix + count + 1;
    LinqOperator *operators = PyMem_Calloc(total, sizeof(LinqOperator));
    if (operators == NULL) {
        return PyErr_NoMemory();
    }
    LinqQuery *query = (LinqQuery *)state->query_type->tp_alloc(state->query_type, 0);
    if (query == NULL) {
        PyMem_Free(operators);
        return NULL;
    }
    query->source = Py_NewRef(producer ? producer->source : source);
    query->operators = operators;
    query->operator_count = total;
    query->identity_projection = identity == Py_True;
    for (Py_ssize_t i = 0; i < prefix; i++) {
        operators[i] = (LinqOperator){LINQ_FILTER, Py_NewRef(producer->operators[i].config)};
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        operators[prefix + i] = (LinqOperator){LINQ_FILTER, Py_NewRef(PyTuple_GET_ITEM(predicates, i))};
    }
    operators[total - 1] = (LinqOperator){LINQ_PROJECT, Py_NewRef(selector)};
    LINQ_COUNT(state, producer ? LINQ_FUSED_QUERIES : LINQ_NORMAL_QUERIES);
    return (PyObject *)query;
}

static PyObject *
linq_query(PyObject *module, PyObject *args)
{
    PyObject *source, *predicates, *selector, *identity;
    LinqState *state = PyModule_GetState(module);
    if (query_arguments(state, args, 0, &source, &predicates, &selector, &identity) < 0) {
        return NULL;
    }
    return query_create(state, source, predicates, selector, identity, NULL);
}

static int
can_fuse(LinqState *state, PyObject *source, PyObject *identity)
{
    if (Py_TYPE(source) != state->query_type || identity != Py_True) {
        return 0;
    }
    LinqQuery *producer = (LinqQuery *)source;
    if (!producer->identity_projection || producer->source == NULL ||
        producer->operator_count < 1) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < producer->operator_count; i++) {
        LinqOperatorKind expected = i == producer->operator_count - 1 ? LINQ_PROJECT : LINQ_FILTER;
        if (producer->operators[i].kind != expected || producer->operators[i].config == NULL) {
            return 0;
        }
    }
    return 1;
}

static PyObject *
linq_fused_query(PyObject *module, PyObject *args)
{
    PyObject *source, *predicates, *selector, *identity;
    LinqState *state = PyModule_GetState(module);
    if (query_arguments(state, args, 1, &source, &predicates, &selector, &identity) < 0) {
        return NULL;
    }
    if (!can_fuse(state, source, identity)) {
        LINQ_COUNT(state, LINQ_FUSION_FALLBACKS);
        return query_create(state, source, predicates, selector, identity, NULL);
    }
    return query_create(state, source, predicates, selector, identity, (LinqQuery *)source);
}

PyDoc_STRVAR(fused_doc,
"_fused_query($module, producer, predicates, selector, identity_projection, /)\n"
"--\n\n"
"Construct a guarded fused query, with normal-construction fallback.");

#ifdef Py_DEBUG
static PyObject *
linq_test_counters(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"enabled", "reset", NULL};
    static const char *names[LINQ_COUNTER_COUNT] = {
        "normal_queries", "fused_queries", "fusion_fallbacks", "query_iterators",
        "filter_iterators", "project_iterators", "project_calls"
    };
    PyObject *enabled = Py_None;
    int reset = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "|$Op:_test_counters", keywords,
                                     &enabled, &reset)) {
        return NULL;
    }
    LinqState *state = PyModule_GetState(module);
    if (enabled != Py_None) {
        int value = PyObject_IsTrue(enabled);
        if (value < 0) return NULL;
        state->test_enabled = value;
    }
    if (reset) memset(state->test_counts, 0, sizeof(state->test_counts));
    PyObject *result = PyDict_New();
    if (result == NULL) return NULL;
    for (int i = 0; i < LINQ_COUNTER_COUNT; i++) {
        PyObject *value = PyLong_FromSsize_t(state->test_counts[i]);
        if (value == NULL) { Py_DECREF(result); return NULL; }
        int ok = PyDict_SetItemString(result, names[i], value);
        Py_DECREF(value);
        if (ok < 0) { Py_DECREF(result); return NULL; }
    }
    if (PyDict_SetItemString(result, "enabled", state->test_enabled ? Py_True : Py_False) < 0) {
        Py_DECREF(result);
        return NULL;
    }
    return result;
}
#endif

PyDoc_STRVAR(query_doc,
"_query($module, source, predicates, selector, identity_projection, /)\n"
"--\n\n"
"Construct a native lazy query without invoking its callbacks.");

static PyObject *
linq_inspect(PyObject *module, PyObject *args, PyObject *kwargs)
{
    static char *keywords[] = {"tree", "filename", "mode", "optimize", "fusion", NULL};
    PyObject *tree;
    PyObject *filename = NULL;
    const char *mode = "exec";
    int optimize = -1;
    int fusion = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwargs, "O|Us$ip:_inspect", keywords,
                                     &tree, &filename, &mode, &optimize, &fusion)) {
        return NULL;
    }
    int compile_mode;
    if (strcmp(mode, "exec") == 0) {
        compile_mode = 0;
    }
    else if (strcmp(mode, "eval") == 0) {
        compile_mode = 1;
    }
    else if (strcmp(mode, "single") == 0) {
        compile_mode = 2;
    }
    else {
        PyErr_SetString(PyExc_ValueError, "mode must be 'exec', 'eval' or 'single'");
        return NULL;
    }
    if (optimize < -1 || optimize > 2) {
        PyErr_SetString(PyExc_ValueError, "invalid optimize value");
        return NULL;
    }
    filename = filename ? Py_NewRef(filename) : PyUnicode_FromString("<linq>");
    if (filename == NULL) {
        return NULL;
    }
    PyObject *result = _PyCompile_LinqInspect(tree, filename, optimize, compile_mode, fusion);
    Py_DECREF(filename);
    return result;
}

PyDoc_STRVAR(inspect_doc,
"_inspect($module, tree, filename='<linq>', mode='exec', *, optimize=-1, fusion=False)\n"
"--\n\n"
"Inspect bound query plans without generating instructions or executing code.");

static PyMethodDef linq_methods[] = {
    {"_query", linq_query, METH_VARARGS, query_doc},
    {"_fused_query", linq_fused_query, METH_VARARGS, fused_doc},
#ifdef Py_DEBUG
    {"_test_counters", _PyCFunction_CAST(linq_test_counters), METH_VARARGS | METH_KEYWORDS,
     "Control debug-only per-interpreter execution counters."},
#endif
    {"_inspect", _PyCFunction_CAST(linq_inspect), METH_VARARGS | METH_KEYWORDS, inspect_doc},
    {NULL, NULL, 0, NULL}
};

static int
linq_exec(PyObject *module)
{
    LinqState *state = PyModule_GetState(module);
    state->query_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &query_spec, NULL);
    if (state->query_type == NULL) {
        return -1;
    }
    return LinqOperators_Init(module, state);
}

static int
linq_traverse(PyObject *module, visitproc visit, void *arg)
{
    LinqState *state = PyModule_GetState(module);
    Py_VISIT(state->query_type);
    Py_VISIT(state->filter_type);
    Py_VISIT(state->project_type);
    return 0;
}

static int
linq_clear(PyObject *module)
{
    LinqState *state = PyModule_GetState(module);
    Py_CLEAR(state->query_type);
    Py_CLEAR(state->filter_type);
    Py_CLEAR(state->project_type);
    return 0;
}

static void
linq_free(void *module)
{
    linq_clear((PyObject *)module);
}

static PyModuleDef_Slot linq_slots[] = {
    {Py_mod_exec, linq_exec},
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
    {Py_mod_gil, Py_MOD_GIL_USED},
    {0, NULL}
};

static PyModuleDef linq_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "_linq",
    .m_doc = "Native query execution support for the compiler.",
    .m_size = sizeof(LinqState),
    .m_methods = linq_methods,
    .m_slots = linq_slots,
    .m_traverse = linq_traverse,
    .m_clear = linq_clear,
    .m_free = linq_free,
};

PyMODINIT_FUNC
PyInit__linq(void)
{
    return PyModuleDef_Init(&linq_module);
}
