#include "Python.h"
#include "pycore_linq.h"

int
_PyLinq_RegisterQuery(struct symtable *st, expr_ty query)
{
    if (st->st_linq_queries == NULL) {
        st->st_linq_queries = PyDict_New();
        if (st->st_linq_queries == NULL) {
            return -1;
        }
    }
    PyObject *key = PyLong_FromVoidPtr(query);
    PyObject *context = PyTuple_Pack(2, st->st_cur,
                                    st->st_private ? st->st_private : Py_None);
    if (key == NULL || context == NULL) {
        Py_XDECREF(key);
        Py_XDECREF(context);
        return -1;
    }
    int result = PyDict_SetItem(st->st_linq_queries, key, context);
    Py_DECREF(key);
    Py_DECREF(context);
    return result;
}

void
_PyLinq_Free(_PyLinqPlans *plans)
{
    if (plans == NULL) {
        return;
    }
    _PyLinq_AnalysisFree(plans->analysis);
    for (Py_ssize_t i = 0; i < plans->count; i++) {
        PyMem_Free(plans->plans[i].operators);
    }
    PyMem_Free(plans->plans);
    Py_XDECREF(plans->index);
    PyMem_Free(plans);
}

static int
plan_index(const _PyLinqPlans *plans, expr_ty query, Py_ssize_t *index)
{
    PyObject *key = PyLong_FromVoidPtr(query);
    if (key == NULL) {
        return -1;
    }
    PyObject *value = PyDict_GetItemWithError(plans->index, key);
    Py_DECREF(key);
    if (value == NULL) {
        if (!PyErr_Occurred()) {
            PyErr_SetString(PyExc_SystemError, "missing LINQ query plan");
        }
        return -1;
    }
    *index = PyLong_AsSsize_t(value);
    return *index == -1 && PyErr_Occurred() ? -1 : 0;
}

const _PyLinqPlan *
_PyLinq_Lookup(_PyLinqPlans *plans, expr_ty query)
{
    Py_ssize_t index;
    if (plan_index(plans, query, &index) < 0) {
        return NULL;
    }
    return &plans->plans[index];
}

static int
validate_source(_PyLinqPlan *plan)
{
    const _PyLinqOperator *op = &plan->operators[0];
    if (op->expression == NULL || op->kind != _PyLinq_SOURCE ||
        op->input != _PyLinq_NO_INPUT || op->result != _PyLinq_SEQUENCE) {
        PyErr_SetString(PyExc_SystemError, "invalid LINQ source operator");
        return -1;
    }
    return 0;
}

static int
validate_clause(_PyLinqPlan *plan, _PyLinqOperator *op,
                struct symtable *st, Py_ssize_t clause)
{
    if (op->expression == NULL ||
        (op->kind != _PyLinq_FILTER && op->kind != _PyLinq_PROJECT) ||
        op->input != _PyLinq_SEQUENCE || op->result != _PyLinq_SEQUENCE) {
        PyErr_SetString(PyExc_SystemError, "invalid LINQ clause operator");
        return -1;
    }
    op->scope_key = _PyLinq_ClauseKey(plan->query, clause);
    PySTEntryObject *scope = _PySymtable_Lookup(st, op->scope_key);
    if (scope == NULL) {
        return -1;
    }
    op->scope = scope;  /* The symbol table retains the entry. */
    Py_DECREF(scope);
    if (scope->ste_type != FunctionBlock || PyList_GET_SIZE(scope->ste_varnames) != 1) {
        PyErr_SetString(PyExc_SystemError, "invalid LINQ clause scope");
        return -1;
    }
    if (op->kind == _PyLinq_PROJECT && op->expression->kind == Name_kind) {
        PyObject *name = _Py_MaybeMangle(plan->private_name, scope,
                                        op->expression->v.Name.id);
        if (name == NULL) {
            return -1;
        }
        int matches = PyObject_RichCompareBool(name,
                            PyList_GET_ITEM(scope->ste_varnames, 0), Py_EQ);
        long symbol = _PyST_GetSymbol(scope, name);
        Py_DECREF(name);
        if (matches < 0) {
            return -1;
        }
        plan->identity_projection = matches && (symbol & DEF_PARAM);
    }
    return 0;
}

static int
build_plan(_PyLinqPlan *plan, struct symtable *st)
{
    expr_ty query = plan->query;
    if (query->kind != QueryExpr_kind || query->v.QueryExpr.target == NULL ||
        query->v.QueryExpr.target->kind != Name_kind ||
        query->v.QueryExpr.target->v.Name.ctx != Store) {
        PyErr_SetString(PyExc_SystemError, "invalid LINQ query plan target");
        return -1;
    }
    Py_ssize_t count = asdl_seq_LEN(query->v.QueryExpr.predicates);
    if (count > PY_SSIZE_T_MAX / (Py_ssize_t)sizeof(_PyLinqOperator) - 2) {
        PyErr_NoMemory();
        return -1;
    }
    plan->operator_count = count + 2;
    plan->operators = PyMem_Calloc(count + 2, sizeof(_PyLinqOperator));
    if (plan->operators == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    plan->operators[0] = (_PyLinqOperator){
        .kind = _PyLinq_SOURCE, .expression = query->v.QueryExpr.source,
        .input = _PyLinq_NO_INPUT, .result = _PyLinq_SEQUENCE,
    };
    if (validate_source(plan) < 0) {
        return -1;
    }
    for (Py_ssize_t i = 0; i <= count; i++) {
        _PyLinqOperator *op = &plan->operators[i + 1];
        op->kind = i == count ? _PyLinq_PROJECT : _PyLinq_FILTER;
        op->input = _PyLinq_SEQUENCE;
        op->result = _PyLinq_SEQUENCE;
        op->expression = i == count ? query->v.QueryExpr.selector :
                                     asdl_seq_GET(query->v.QueryExpr.predicates, i);
        if (validate_clause(plan, op, st, i) < 0) {
            return -1;
        }
    }
    return 0;
}

_PyLinqPlans *
_PyLinq_Build(struct symtable *st)
{
    _PyLinqPlans *plans = PyMem_Calloc(1, sizeof(*plans));
    if (plans == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    plans->index = PyDict_New();
    if (plans->index == NULL) {
        goto error;
    }
    plans->count = st->st_linq_queries ? PyDict_GET_SIZE(st->st_linq_queries) : 0;
    if (plans->count == 0) {
        return plans;
    }
    plans->plans = PyMem_Calloc(plans->count, sizeof(_PyLinqPlan));
    if (plans->plans == NULL) {
        plans->count = 0;
        PyErr_NoMemory();
        goto error;
    }
    PyObject *key, *context;
    Py_ssize_t pos = 0, i = 0;
    while (PyDict_Next(st->st_linq_queries, &pos, &key, &context)) {
        _PyLinqPlan *plan = &plans->plans[i];
        plan->query = PyLong_AsVoidPtr(key);
        if (plan->query == NULL && PyErr_Occurred()) {
            goto error;
        }
        plan->owner = (PySTEntryObject *)PyTuple_GET_ITEM(context, 0);
        PyObject *private = PyTuple_GET_ITEM(context, 1);
        plan->private_name = private == Py_None ? NULL : private;
        plan->input_plan = -1;
        plan->mode = _PyLinq_NORMAL;
        PyObject *index = PyLong_FromSsize_t(i++);
        if (index == NULL) {
            goto error;
        }
        int result = PyDict_SetItem(plans->index, key, index);
        Py_DECREF(index);
        if (result < 0 || build_plan(plan, st) < 0) {
            goto error;
        }
    }
    for (i = 0; i < plans->count; i++) {
        _PyLinqPlan *plan = &plans->plans[i];
        expr_ty source = plan->operators[0].expression;
        if (source->kind == QueryExpr_kind &&
            plan_index(plans, source, &plan->input_plan) < 0) {
            goto error;
        }
    }
    return plans;
error:
    _PyLinq_Free(plans);
    return NULL;
}

static const char *
expression_name(expr_ty expression)
{
#define EXPR_NAME(KIND) case KIND##_kind: return #KIND
    switch (expression->kind) {
        EXPR_NAME(BoolOp); EXPR_NAME(NamedExpr); EXPR_NAME(BinOp);
        EXPR_NAME(UnaryOp); EXPR_NAME(Lambda); EXPR_NAME(IfExp);
        EXPR_NAME(Dict); EXPR_NAME(Set); EXPR_NAME(ListComp);
        EXPR_NAME(SetComp); EXPR_NAME(DictComp); EXPR_NAME(GeneratorExp);
        EXPR_NAME(Await); EXPR_NAME(Yield); EXPR_NAME(YieldFrom);
        EXPR_NAME(Compare); EXPR_NAME(Call); EXPR_NAME(FormattedValue);
        EXPR_NAME(Interpolation); EXPR_NAME(JoinedStr); EXPR_NAME(TemplateStr);
        EXPR_NAME(Constant); EXPR_NAME(Attribute); EXPR_NAME(Subscript);
        EXPR_NAME(Starred); EXPR_NAME(Name); EXPR_NAME(List);
        EXPR_NAME(Tuple); EXPR_NAME(Slice); EXPR_NAME(QueryExpr);
    }
#undef EXPR_NAME
    return "unknown";
}

static const char *
category_name(_PyLinqCategory category)
{
    switch (category) {
        case _PyLinq_NO_INPUT: return "none";
        case _PyLinq_SEQUENCE: return "sequence";
        default: return NULL;
    }
}

static const char *
binding_name(int scope)
{
    switch (scope) {
        case LOCAL: return "local";
        case GLOBAL_EXPLICIT: return "global_explicit";
        case GLOBAL_IMPLICIT: return "global_implicit";
        case FREE: return "free";
        case CELL: return "cell";
        default: return "unresolved";
    }
}

static PyObject *
describe_scope(PySTEntryObject *scope, PyObject *ids)
{
    PyObject *id = PyDict_GetItemWithError(ids, scope->ste_id);
    if (id == NULL) {
        if (PyErr_Occurred()) {
            return NULL;
        }
        id = PyLong_FromSsize_t(PyDict_GET_SIZE(ids));
        if (id == NULL) {
            return NULL;
        }
        int result = PyDict_SetItem(ids, scope->ste_id, id);
        Py_DECREF(id);
        if (result < 0) {
            return NULL;
        }
        id = PyDict_GetItemWithError(ids, scope->ste_id);
        assert(id != NULL);
    }
    return Py_BuildValue("{s:O,s:O,s:(iiii)}", "id", id,
                         "name", scope->ste_name, "location",
                         scope->ste_loc.lineno, scope->ste_loc.col_offset,
                         scope->ste_loc.end_lineno, scope->ste_loc.end_col_offset);
}

static PyObject *
describe_operator(const _PyLinqPlan *plan, const _PyLinqOperator *op, PyObject *ids)
{
    const char *kind;
    switch (op->kind) {
        case _PyLinq_SOURCE: kind = "Source"; break;
        case _PyLinq_FILTER: kind = "Filter"; break;
        case _PyLinq_PROJECT: kind = "Project"; break;
        default:
            PyErr_SetString(PyExc_SystemError, "unknown LINQ IR operator");
            return NULL;
    }
    expr_ty e = op->expression;
    const char *input = category_name(op->input);
    const char *output = category_name(op->result);
    if (input == NULL || output == NULL) {
        PyErr_SetString(PyExc_SystemError, "unknown LINQ IR category");
        return NULL;
    }
    PyObject *result = Py_BuildValue("{s:s,s:s,s:s,s:s,s:(iiii)}",
        "kind", kind, "input", input, "result", output, "expression", expression_name(e),
        "location", e->lineno, e->col_offset, e->end_lineno, e->end_col_offset);
    if (result == NULL) {
        return NULL;
    }
    if (op->kind == _PyLinq_SOURCE) {
        if (plan->input_plan >= 0) {
            PyObject *input = PyLong_FromSsize_t(plan->input_plan);
            if (input == NULL) {
                goto error;
            }
            int ok = PyDict_SetItemString(result, "input_plan", input);
            Py_DECREF(input);
            if (ok < 0) {
                goto error;
            }
        }
        if (e->kind == Name_kind) {
            PyObject *name = _Py_MaybeMangle(plan->private_name, plan->owner, e->v.Name.id);
            if (name == NULL) {
                goto error;
            }
            int scope = _PyST_GetScope(plan->owner, name);
            PyObject *binding = Py_BuildValue("{s:O,s:s}", "name", name,
                                             "scope", binding_name(scope));
            Py_DECREF(name);
            if (binding == NULL) {
                goto error;
            }
            int ok = PyDict_SetItemString(result, "binding", binding);
            Py_DECREF(binding);
            if (ok < 0) {
                goto error;
            }
        }
        return result;
    }
    PyObject *scope = describe_scope(op->scope, ids);
    if (scope == NULL) {
        goto error;
    }
    int ok = PyDict_SetItemString(result, "scope", scope);
    Py_DECREF(scope);
    if (ok < 0 || PyDict_SetItemString(result, "parameter",
                                     PyList_GET_ITEM(op->scope->ste_varnames, 0)) < 0) {
        goto error;
    }
    PyObject *captures = PyList_New(0);
    if (captures == NULL) {
        goto error;
    }
    Py_ssize_t pos = 0;
    PyObject *name, *flags;
    while (PyDict_Next(op->scope->ste_symbols, &pos, &name, &flags)) {
        if (_PyST_GetScope(op->scope, name) == FREE && PyList_Append(captures, name) < 0) {
            Py_DECREF(captures);
            goto error;
        }
    }
    ok = PyList_Sort(captures);
    if (ok == 0) {
        ok = PyDict_SetItemString(result, "captures", captures);
    }
    Py_DECREF(captures);
    if (ok < 0) {
        goto error;
    }
    if (op->kind == _PyLinq_PROJECT && PyDict_SetItemString(result, "identity",
                                    plan->identity_projection ? Py_True : Py_False) < 0) {
        goto error;
    }
    return result;
error:
    Py_DECREF(result);
    return NULL;
}

PyObject *
_PyLinq_Describe(const _PyLinqPlans *plans)
{
    PyObject *queries = PyList_New(plans->count);
    PyObject *ids = PyDict_New();
    PyObject *result = NULL;
    if (queries == NULL || ids == NULL) {
        goto done;
    }
    for (Py_ssize_t i = 0; i < plans->count; i++) {
        const _PyLinqPlan *plan = &plans->plans[i];
        PyObject *owner = describe_scope(plan->owner, ids);
        PyObject *ops = PyList_New(plan->operator_count);
        if (owner == NULL || ops == NULL) {
            Py_XDECREF(owner);
            Py_XDECREF(ops);
            goto done;
        }
        for (Py_ssize_t j = 0; j < plan->operator_count; j++) {
            PyObject *op = describe_operator(plan, &plan->operators[j], ids);
            if (op == NULL) {
                Py_DECREF(owner);
                Py_DECREF(ops);
                goto done;
            }
            PyList_SET_ITEM(ops, j, op);
        }
        expr_ty e = plan->query;
        PyObject *description = Py_BuildValue("{s:n,s:O,s:(iiii),s:s,s:O}",
            "id", i, "owner", owner, "location",
            e->lineno, e->col_offset, e->end_lineno, e->end_col_offset,
            "mode", "normal", "operators", ops);
        Py_DECREF(owner);
        Py_DECREF(ops);
        if (description == NULL) {
            goto done;
        }
        PyList_SET_ITEM(queries, i, description);
    }
    PyObject *analysis = _PyLinq_AnalysisDescribe(plans);
    if (analysis != NULL) {
        result = Py_BuildValue("{s:O,s:O}", "queries", queries, "analysis", analysis);
        Py_DECREF(analysis);
    }
done:
    Py_XDECREF(queries);
    Py_XDECREF(ids);
    return result;
}
