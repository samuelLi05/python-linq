#include "Python.h"
#include "_linq/linq_runtime.h"

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

static PyObject *
linq_query(PyObject *module, PyObject *args)
{
    PyObject *source, *predicates, *selector, *identity;
    if (!PyArg_ParseTuple(args, "OOOO:_query", &source, &predicates, &selector, &identity)) {
        return NULL;
    }
    LinqState *state = PyModule_GetState(module);
    if (!PyList_Check(source) && Py_TYPE(source) != state->query_type) {
        PyErr_SetString(PyExc_TypeError, "LINQ source must be a list or native query");
        return NULL;
    }
    if (!PyTuple_Check(predicates)) {
        PyErr_SetString(PyExc_TypeError, "LINQ predicates must be a tuple of callables");
        return NULL;
    }
    Py_ssize_t count = PyTuple_GET_SIZE(predicates);
    for (Py_ssize_t i = 0; i < count; i++) {
        if (!PyCallable_Check(PyTuple_GET_ITEM(predicates, i))) {
            PyErr_SetString(PyExc_TypeError, "LINQ predicate must be callable");
            return NULL;
        }
    }
    if (!PyCallable_Check(selector)) {
        PyErr_SetString(PyExc_TypeError, "LINQ selector must be callable");
        return NULL;
    }
    if (!PyBool_Check(identity)) {
        PyErr_SetString(PyExc_TypeError, "LINQ identity metadata must be a bool");
        return NULL;
    }
    if (count > PY_SSIZE_T_MAX / (Py_ssize_t)sizeof(LinqOperator) - 1) {
        return PyErr_NoMemory();
    }
    LinqOperator *operators = PyMem_Calloc(count + 1, sizeof(LinqOperator));
    if (operators == NULL) {
        return PyErr_NoMemory();
    }
    LinqQuery *query = (LinqQuery *)state->query_type->tp_alloc(state->query_type, 0);
    if (query == NULL) {
        PyMem_Free(operators);
        return NULL;
    }
    query->source = Py_NewRef(source);
    query->operators = operators;
    query->operator_count = count + 1;
    query->identity_projection = identity == Py_True;
    for (Py_ssize_t i = 0; i < count; i++) {
        operators[i].kind = LINQ_FILTER;
        operators[i].config = Py_NewRef(PyTuple_GET_ITEM(predicates, i));
    }
    operators[count].kind = LINQ_PROJECT;
    operators[count].config = Py_NewRef(selector);
    return (PyObject *)query;
}

PyDoc_STRVAR(query_doc,
"_query($module, source, predicates, selector, identity_projection, /)\n"
"--\n\n"
"Construct a native lazy query without invoking its callbacks.");

static PyMethodDef linq_methods[] = {
    {"_query", linq_query, METH_VARARGS, query_doc},
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
