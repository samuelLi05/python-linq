#ifndef LINQ_RUNTIME_H
#define LINQ_RUNTIME_H

#include "Python.h"

typedef enum {
    LINQ_FILTER,
    LINQ_PROJECT,
    LINQ_OPERATOR_COUNT
} LinqOperatorKind;

typedef struct {
    LinqOperatorKind kind;
    PyObject *config;
} LinqOperator;

typedef enum {
    LINQ_NORMAL_QUERIES, LINQ_FUSED_QUERIES, LINQ_FUSION_FALLBACKS,
    LINQ_QUERY_ITERATORS, LINQ_FILTER_ITERATORS, LINQ_PROJECT_ITERATORS,
    LINQ_PROJECT_CALLS, LINQ_COUNTER_COUNT
} LinqCounter;

typedef struct {
    PyTypeObject *query_type;
    PyTypeObject *filter_type;
    PyTypeObject *project_type;
#ifdef Py_DEBUG
    int test_enabled;
    Py_ssize_t test_counts[LINQ_COUNTER_COUNT];
#endif
} LinqState;

#ifdef Py_DEBUG
static inline void
LINQ_COUNT(LinqState *state, LinqCounter counter)
{
    if (state->test_enabled && state->test_counts[counter] < PY_SSIZE_T_MAX) {
        state->test_counts[counter]++;
    }
}
#else
#  define LINQ_COUNT(STATE, COUNTER) ((void)0)
#endif

int LinqOperators_Init(PyObject *module, LinqState *state);
PyObject *LinqOperator_Iter(LinqState *state, const LinqOperator *op,
                            PyObject *upstream);

#endif
