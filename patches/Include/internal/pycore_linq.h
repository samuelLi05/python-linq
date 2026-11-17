#ifndef Py_INTERNAL_LINQ_H
#define Py_INTERNAL_LINQ_H
#ifndef Py_BUILD_CORE
#  error "this header requires Py_BUILD_CORE define"
#endif

#include "pycore_ast.h"
#include "pycore_symtable.h"

typedef enum {
    _PyLinq_SOURCE,
    _PyLinq_FILTER,
    _PyLinq_PROJECT,
    _PyLinq_OPERATOR_COUNT
} _PyLinqOpKind;

typedef enum {
    _PyLinq_NORMAL
} _PyLinqExecutionMode;

typedef enum {
    _PyLinq_NO_INPUT,
    _PyLinq_SEQUENCE
} _PyLinqCategory;

typedef struct {
    _PyLinqOpKind kind;
    _PyLinqCategory input;
    _PyLinqCategory result;
    expr_ty expression;
    void *scope_key;
    PySTEntryObject *scope;
} _PyLinqOperator;

typedef struct {
    expr_ty query;
    PySTEntryObject *owner;
    PyObject *private_name;
    _PyLinqOperator *operators;
    Py_ssize_t operator_count;
    Py_ssize_t input_plan;
    int identity_projection;
    _PyLinqExecutionMode mode;
} _PyLinqPlan;

typedef struct _PyLinqAnalysis _PyLinqAnalysis;
typedef enum { _PyLinq_ELIGIBLE, _PyLinq_INELIGIBLE, _PyLinq_UNKNOWN } _PyLinqStatus;
typedef struct {
    _PyLinqStatus status;
    const char *reason;
    Py_ssize_t function, source_use, producer_definition, producer_plan, extra_use;
} _PyLinqDecision;

typedef struct {
    _PyLinqAnalysis *analysis;
    PyObject *index;
    _PyLinqPlan *plans;
    Py_ssize_t count;
} _PyLinqPlans;

/* Slot addresses distinguish a clause from a scope-forming body expression. */
static inline void *
_PyLinq_ClauseKey(expr_ty query, Py_ssize_t clause)
{
    Py_ssize_t count = asdl_seq_LEN(query->v.QueryExpr.predicates);
    assert(clause >= 0 && clause <= count);
    if (clause < count) {
        return &query->v.QueryExpr.predicates->typed_elements[clause];
    }
    return &query->v.QueryExpr.selector;
}

int _PyLinq_RegisterFunction(struct symtable *, stmt_ty);
int _PyLinq_Analyze(struct symtable *, _PyLinqPlans *);
void _PyLinq_AnalysisFree(_PyLinqAnalysis *);
PyObject *_PyLinq_AnalysisDescribe(const _PyLinqPlans *);
const _PyLinqDecision *_PyLinq_Decision(_PyLinqPlans *, expr_ty);

int _PyLinq_RegisterQuery(struct symtable *st, expr_ty query);
_PyLinqPlans *_PyLinq_Build(struct symtable *st);
void _PyLinq_Free(_PyLinqPlans *plans);
const _PyLinqPlan *_PyLinq_Lookup(_PyLinqPlans *plans, expr_ty query);
PyObject *_PyLinq_Describe(const _PyLinqPlans *plans);

#endif
