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

typedef struct {
    PyTypeObject *query_type;
    PyTypeObject *filter_type;
    PyTypeObject *project_type;
} LinqState;

int LinqOperators_Init(PyObject *module, LinqState *state);
PyObject *LinqOperator_Iter(LinqState *state, const LinqOperator *op,
                            PyObject *upstream);

#endif
