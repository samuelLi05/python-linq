#include "Python.h"
#include "pycore_linq.h"

static int
supported_plan(const _PyLinqPlan *plan)
{
    if (plan->operator_count < 2 || !plan->identity_projection ||
        plan->operators[0].kind != _PyLinq_SOURCE) {
        return 0;
    }
    for (Py_ssize_t i = 1; i < plan->operator_count; i++) {
        const _PyLinqOperator *op = &plan->operators[i];
        _PyLinqOpKind expected = i == plan->operator_count - 1 ?
                                 _PyLinq_PROJECT : _PyLinq_FILTER;
        if (op->kind != expected || op->input != _PyLinq_SEQUENCE ||
            op->result != _PyLinq_SEQUENCE) {
            return 0;
        }
    }
    return 1;
}

int
_PyLinq_Optimize(_PyLinqPlans *plans, int enabled)
{
    plans->fusion_enabled = enabled != 0;
    for (Py_ssize_t i = 0; i < plans->count; i++) {
        plans->plans[i].mode = _PyLinq_NORMAL;
        plans->plans[i].fused_producer = -1;
    }
    if (!enabled) {
        return 0;
    }
    for (Py_ssize_t i = 0; i < plans->count; i++) {
        _PyLinqPlan *consumer = &plans->plans[i];
        const _PyLinqDecision *decision = _PyLinq_Decision(plans, consumer->query);
        if (decision == NULL) {
            return -1;
        }
        if (decision->status != _PyLinq_ELIGIBLE) {
            continue;
        }
        Py_ssize_t producer = decision->producer_plan;
        if (producer < 0 || producer >= plans->count || producer == i) {
            PyErr_SetString(PyExc_SystemError, "invalid LINQ fusion proof");
            return -1;
        }
        if (!supported_plan(consumer) || !supported_plan(&plans->plans[producer])) {
            continue;
        }
        consumer->mode = _PyLinq_FUSED;
        consumer->fused_producer = producer;
    }
    return 0;
}
