#include "linq_runtime.h"

typedef struct {
    PyObject_HEAD
    PyObject *upstream;
    PyObject *callback;
    int running;
} LinqStage;

static int
stage_traverse(PyObject *object, visitproc visit, void *arg)
{
    LinqStage *stage = (LinqStage *)object;
    Py_VISIT(Py_TYPE(stage));
    Py_VISIT(stage->upstream);
    Py_VISIT(stage->callback);
    return 0;
}

static int
stage_clear(PyObject *object)
{
    LinqStage *stage = (LinqStage *)object;
    Py_CLEAR(stage->upstream);
    Py_CLEAR(stage->callback);
    return 0;
}

static void
stage_dealloc(PyObject *object)
{
    PyTypeObject *type = Py_TYPE(object);
    PyObject_GC_UnTrack(object);
    stage_clear(object);
    type->tp_free(object);
    Py_DECREF(type);
}

static void
callback_error(void)
{
    if (!PyErr_ExceptionMatches(PyExc_StopIteration)) {
        return;
    }
    PyObject *cause = PyErr_GetRaisedException();
    PyObject *error = PyObject_CallFunction(PyExc_RuntimeError, "s",
                                          "LINQ callback raised StopIteration");
    if (error == NULL) {
        Py_DECREF(cause);
        return;
    }
    PyException_SetCause(error, Py_NewRef(cause));
    PyException_SetContext(error, cause);
    PyErr_SetRaisedException(error);
}

static PyObject *
stage_finish(LinqStage *stage, int from_callback)
{
    if (from_callback) {
        callback_error();
    }
    PyObject *error = PyErr_GetRaisedException();
    stage_clear((PyObject *)stage);
    stage->running = 0;
    PyErr_SetRaisedException(error);
    Py_LeaveRecursiveCall();
    return NULL;
}

static int
stage_enter(LinqStage *stage)
{
    if (stage->running) {
        PyErr_SetString(PyExc_RuntimeError, "LINQ iterator is already executing");
        return 0;
    }
    if (stage->upstream == NULL) {
        return 0;
    }
    if (Py_EnterRecursiveCall(" while advancing a LINQ iterator")) {
        return 0;
    }
    stage->running = 1;
    return 1;
}

static PyObject *
filter_next(PyObject *object)
{
    LinqStage *stage = (LinqStage *)object;
    if (!stage_enter(stage)) {
        return NULL;
    }
    for (;;) {
        PyObject *item = PyIter_Next(stage->upstream);
        if (item == NULL) {
            return stage_finish(stage, 0);
        }
        PyObject *result = PyObject_CallOneArg(stage->callback, item);
        if (result == NULL) {
            Py_DECREF(item);
            return stage_finish(stage, 1);
        }
        int keep = PyObject_IsTrue(result);
        Py_DECREF(result);
        if (keep < 0) {
            Py_DECREF(item);
            return stage_finish(stage, 1);
        }
        if (keep) {
            stage->running = 0;
            Py_LeaveRecursiveCall();
            return item;
        }
        Py_DECREF(item);
    }
}

static PyObject *
project_next(PyObject *object)
{
    LinqStage *stage = (LinqStage *)object;
    if (!stage_enter(stage)) {
        return NULL;
    }
    PyObject *item = PyIter_Next(stage->upstream);
    if (item == NULL) {
        return stage_finish(stage, 0);
    }
    PyObject *result = PyObject_CallOneArg(stage->callback, item);
    Py_DECREF(item);
    if (result == NULL) {
        return stage_finish(stage, 1);
    }
    stage->running = 0;
    Py_LeaveRecursiveCall();
    return result;
}

static PyType_Slot filter_slots[] = {
    {Py_tp_dealloc, stage_dealloc},
    {Py_tp_traverse, stage_traverse},
    {Py_tp_clear, stage_clear},
    {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, filter_next},
    {0, NULL}
};

static PyType_Slot project_slots[] = {
    {Py_tp_dealloc, stage_dealloc},
    {Py_tp_traverse, stage_traverse},
    {Py_tp_clear, stage_clear},
    {Py_tp_iter, PyObject_SelfIter},
    {Py_tp_iternext, project_next},
    {0, NULL}
};

#define STAGE_FLAGS (Py_TPFLAGS_DEFAULT | Py_TPFLAGS_HAVE_GC | \
                    Py_TPFLAGS_IMMUTABLETYPE | Py_TPFLAGS_DISALLOW_INSTANTIATION)

static PyType_Spec filter_spec = {
    .name = "_linq._FilterIterator",
    .basicsize = sizeof(LinqStage),
    .flags = STAGE_FLAGS,
    .slots = filter_slots,
};

static PyType_Spec project_spec = {
    .name = "_linq._ProjectIterator",
    .basicsize = sizeof(LinqStage),
    .flags = STAGE_FLAGS,
    .slots = project_slots,
};

int
LinqOperators_Init(PyObject *module, LinqState *state)
{
    state->filter_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &filter_spec, NULL);
    if (state->filter_type == NULL) {
        return -1;
    }
    state->project_type = (PyTypeObject *)PyType_FromModuleAndSpec(module, &project_spec, NULL);
    return state->project_type == NULL ? -1 : 0;
}

static PyObject *
stage_new(PyTypeObject *type, PyObject *config, PyObject *upstream)
{
    LinqStage *stage = (LinqStage *)type->tp_alloc(type, 0);
    if (stage == NULL) {
        return NULL;
    }
    stage->upstream = Py_NewRef(upstream);
    stage->callback = Py_NewRef(config);
    return (PyObject *)stage;
}

static PyObject *
filter_iter(LinqState *state, PyObject *config, PyObject *upstream)
{
    return stage_new(state->filter_type, config, upstream);
}

static PyObject *
project_iter(LinqState *state, PyObject *config, PyObject *upstream)
{
    return stage_new(state->project_type, config, upstream);
}

PyObject *
LinqOperator_Iter(LinqState *state, const LinqOperator *op, PyObject *upstream)
{
    typedef PyObject *(*StageFactory)(LinqState *, PyObject *, PyObject *);
    static const StageFactory factories[LINQ_OPERATOR_COUNT] = {
        [LINQ_FILTER] = filter_iter,
        [LINQ_PROJECT] = project_iter,
    };
    if (op->kind < 0 || op->kind >= LINQ_OPERATOR_COUNT || factories[op->kind] == NULL) {
        PyErr_SetString(PyExc_SystemError, "unknown LINQ operator");
        return NULL;
    }
    return factories[op->kind](state, op->config, upstream);
}
