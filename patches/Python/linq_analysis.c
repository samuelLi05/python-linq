#include "Python.h"
#include "pycore_linq.h"
#include "pycore_unicodeobject.h"
#include <stdint.h>

/* Bit indices are definition/block IDs, never executable variable names. */
typedef struct { uint64_t *words; Py_ssize_t count; } Bits;
typedef struct {
    PyObject *name;
    long flags;
    Bits definitions;
} Binding;
typedef enum { PARAMETER, UNBOUND, ASSIGNMENT } DefKind;
typedef struct {
    Py_ssize_t binding, block, position, query;
    DefKind kind;
    stmt_ty statement;
} Definition;
typedef enum { READ, SOURCE, ALIAS, ESCAPE } UseKind;
typedef struct {
    Py_ssize_t binding, block, position, query;
    UseKind kind;
    expr_ty expression;
    Bits reaching;
} Use;
typedef struct { int is_definition; Py_ssize_t index; } Event;
typedef struct {
    stmt_ty *statements;
    Py_ssize_t nstatements, cstatements;
    Event *events;
    Py_ssize_t nevents, cevents;
    Py_ssize_t successors[2], nsuccessors;
    Py_ssize_t *predecessors, npredecessors, cpredecessors;
    int reachable;
    Bits gen, kill, in, out, dominators;
} Block;
typedef struct { const char *code; _Py_SourceLocation location; } Hazard;
typedef struct {
    stmt_ty node;
    PySTEntryObject *scope;
    PyObject *private_name;
    _PyLinqPlans *plans;
    Binding *bindings;
    Py_ssize_t nbindings, cbindings;
    Definition *definitions;
    Py_ssize_t ndefinitions, cdefinitions;
    Use *uses;
    Py_ssize_t nuses, cuses;
    Block *blocks;
    Py_ssize_t nblocks, cblocks;
    Hazard *hazards;
    Py_ssize_t nhazards, chazards;
} Function;
struct _PyLinqAnalysis {
    Function *functions;
    Py_ssize_t count;
    _PyLinqDecision *decisions;
};

static int
reserve(void **items, Py_ssize_t *capacity, Py_ssize_t count, size_t size)
{
    if (count < *capacity) return 0;
    Py_ssize_t next = *capacity ? *capacity : 8;
    if (next > PY_SSIZE_T_MAX / 2 || (size_t)(next * 2) > SIZE_MAX / size) {
        PyErr_NoMemory();
        return -1;
    }
    next *= 2;
    void *allocation = PyMem_Realloc(*items, (size_t)next * size);
    if (allocation == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    *items = allocation;
    *capacity = next;
    return 0;
}
#define SPACE(F, FIELD, N, C) reserve((void **)&(F)->FIELD, &(F)->C, (F)->N, sizeof(*(F)->FIELD))

static int
bits_new(Bits *bits, Py_ssize_t size)
{
    bits->count = size / 64 + (size % 64 != 0);
    if (bits->count == 0) return 0;
    bits->words = PyMem_Calloc(bits->count, sizeof(uint64_t));
    if (bits->words == NULL) {
        PyErr_NoMemory();
        return -1;
    }
    return 0;
}
static void bits_free(Bits *b) { PyMem_Free(b->words); }
static void bits_zero(Bits b) { if (b.count) memset(b.words, 0, b.count * sizeof(uint64_t)); }
static void bits_add(Bits b, Py_ssize_t i) { b.words[i / 64] |= UINT64_C(1) << (i % 64); }
static int bits_has(Bits b, Py_ssize_t i) { return (b.words[i / 64] >> (i % 64)) & 1; }
static void bits_copy(Bits dst, Bits src) { if (src.count) memcpy(dst.words, src.words, src.count * sizeof(uint64_t)); }
static int bits_equal(Bits a, Bits b) { return !a.count || memcmp(a.words, b.words, a.count * sizeof(uint64_t)) == 0; }
static void bits_union(Bits a, Bits b) { for (Py_ssize_t i = 0; i < a.count; i++) a.words[i] |= b.words[i]; }
static void bits_intersect(Bits a, Bits b) { for (Py_ssize_t i = 0; i < a.count; i++) a.words[i] &= b.words[i]; }
static void bits_remove(Bits a, Bits b) { for (Py_ssize_t i = 0; i < a.count; i++) a.words[i] &= ~b.words[i]; }

int
_PyLinq_RegisterFunction(struct symtable *st, stmt_ty node)
{
    if (st->st_linq_functions == NULL) {
        st->st_linq_functions = PyDict_New();
        if (st->st_linq_functions == NULL) return -1;
    }
    PyObject *key = PyLong_FromVoidPtr(node);
    PyObject *context = PyTuple_Pack(2, st->st_cur,
                                    st->st_private ? st->st_private : Py_None);
    if (key == NULL || context == NULL) {
        Py_XDECREF(key); Py_XDECREF(context);
        return -1;
    }
    int result = PyDict_SetItem(st->st_linq_functions, key, context);
    Py_DECREF(key); Py_DECREF(context);
    return result;
}

static void
function_free(Function *f)
{
    for (Py_ssize_t i = 0; i < f->nbindings; i++) bits_free(&f->bindings[i].definitions);
    for (Py_ssize_t i = 0; i < f->nuses; i++) bits_free(&f->uses[i].reaching);
    for (Py_ssize_t i = 0; i < f->nblocks; i++) {
        Block *b = &f->blocks[i];
        PyMem_Free(b->statements); PyMem_Free(b->events); PyMem_Free(b->predecessors);
        bits_free(&b->gen); bits_free(&b->kill); bits_free(&b->in);
        bits_free(&b->out); bits_free(&b->dominators);
    }
    PyMem_Free(f->bindings); PyMem_Free(f->definitions); PyMem_Free(f->uses);
    PyMem_Free(f->blocks); PyMem_Free(f->hazards);
}
void
_PyLinq_AnalysisFree(_PyLinqAnalysis *analysis)
{
    if (analysis == NULL) return;
    for (Py_ssize_t i = 0; i < analysis->count; i++) function_free(&analysis->functions[i]);
    PyMem_Free(analysis->functions); PyMem_Free(analysis->decisions); PyMem_Free(analysis);
}

static int
hazard(Function *f, const char *code, _Py_SourceLocation location)
{
    if (SPACE(f, hazards, nhazards, chazards) < 0) return -1;
    f->hazards[f->nhazards++] = (Hazard){code, location};
    return 0;
}

static Py_ssize_t
query_id(Function *f, expr_ty query)
{
    for (Py_ssize_t i = 0; i < f->plans->count; i++) {
        if (f->plans->plans[i].query == query) return i;
    }
    return -1;
}

static int
binding_id(Function *f, PyObject *raw, Py_ssize_t *result)
{
    PyObject *name = _Py_MaybeMangle(f->private_name, f->scope, raw);
    if (name == NULL) return -1;
    *result = -1;
    for (Py_ssize_t i = 0; i < f->nbindings; i++) {
        int equal = PyObject_RichCompareBool(name, f->bindings[i].name, Py_EQ);
        if (equal < 0) { Py_DECREF(name); return -1; }
        if (equal) { *result = i; break; }
    }
    Py_DECREF(name);
    return 0;
}

static Py_ssize_t
new_block(Function *f)
{
    if (SPACE(f, blocks, nblocks, cblocks) < 0) return -1;
    Py_ssize_t id = f->nblocks++;
    memset(&f->blocks[id], 0, sizeof(Block));
    return id;
}
static int
edge(Function *f, Py_ssize_t from, Py_ssize_t to)
{
    Block *b = &f->blocks[to];
    if (SPACE(b, predecessors, npredecessors, cpredecessors) < 0) return -1;
    assert(f->blocks[from].nsuccessors < 2);
    f->blocks[from].successors[f->blocks[from].nsuccessors++] = to;
    b->predecessors[b->npredecessors++] = from;
    return 0;
}
static int
add_event(Function *f, Py_ssize_t block, int definition, Py_ssize_t index)
{
    Block *b = &f->blocks[block];
    if (SPACE(b, events, nevents, cevents) < 0) return -1;
    b->events[b->nevents++] = (Event){definition, index};
    return 0;
}

static int
add_definition(Function *f, Py_ssize_t binding, stmt_ty statement,
               Py_ssize_t block, Py_ssize_t position, expr_ty value)
{
    if (SPACE(f, definitions, ndefinitions, cdefinitions) < 0) return -1;
    Py_ssize_t id = f->ndefinitions++;
    f->definitions[id] = (Definition){
        .binding = binding, .block = block, .position = position,
        .query = value && value->kind == QueryExpr_kind ? query_id(f, value) : -1,
        .kind = statement ? ASSIGNMENT :
                 (f->bindings[binding].flags & DEF_PARAM ? PARAMETER : UNBOUND),
        .statement = statement,
    };
    return statement ? add_event(f, block, 1, id) : 0;
}

static int
init_bindings(Function *f)
{
    PyObject *names = PyDict_Keys(f->scope->ste_symbols);
    if (names == NULL) return -1;
    if (PyList_Sort(names) < 0) { Py_DECREF(names); return -1; }
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(names); i++) {
        PyObject *name = PyList_GET_ITEM(names, i);
        int scope = _PyST_GetScope(f->scope, name);
        if (scope != LOCAL && scope != CELL) continue;
        if (SPACE(f, bindings, nbindings, cbindings) < 0) { Py_DECREF(names); return -1; }
        Py_ssize_t id = f->nbindings++;
        f->bindings[id] = (Binding){.name = name, .flags = _PyST_GetSymbol(f->scope, name)};
        if (add_definition(f, id, NULL, -1, -1, NULL) < 0) { Py_DECREF(names); return -1; }
    }
    /* Names remain owned by the symbol table. */
    Py_DECREF(names);
    return 0;
}

static int
reflective_name(PyObject *name)
{
    static const char *names[] = {"exec", "eval", "locals", "globals", "vars", "dir", NULL};
    for (int i = 0; names[i]; i++) if (_PyUnicode_EqualToASCIIString(name, names[i])) return 1;
    return 0;
}
static int
reflective_attribute(PyObject *name)
{
    static const char *names[] = {"_getframe", "currentframe", "f_locals", "f_globals", NULL};
    for (int i = 0; names[i]; i++) if (_PyUnicode_EqualToASCIIString(name, names[i])) return 1;
    return 0;
}

static int walk_expr(Function *, expr_ty, Py_ssize_t, Py_ssize_t, int, UseKind, Py_ssize_t);
static int
walk_seq(Function *f, asdl_expr_seq *seq, Py_ssize_t block, Py_ssize_t pos, int deferred)
{
    for (Py_ssize_t i = 0; i < asdl_seq_LEN(seq); i++) {
        if (walk_expr(f, asdl_seq_GET(seq, i), block, pos, deferred, READ, -1) < 0) return -1;
    }
    return 0;
}

static int
walk_expr_impl(Function *f, expr_ty e, Py_ssize_t block, Py_ssize_t pos,
               int deferred, UseKind use_kind, Py_ssize_t source_query)
{
#define WALK(E) do { if (walk_expr(f, (E), block, pos, deferred, READ, -1) < 0) return -1; } while (0)
#define SEQ(S) do { if (walk_seq(f, (S), block, pos, deferred) < 0) return -1; } while (0)
    switch (e->kind) {
        case Name_kind: {
            if (e->v.Name.ctx != Load) return 0;
            if (reflective_name(e->v.Name.id) && hazard(f, "dynamic_namespace_access", SRC_LOCATION_FROM_AST(e)) < 0) return -1;
            if (deferred) return 0;
            Py_ssize_t binding;
            if (binding_id(f, e->v.Name.id, &binding) < 0) return -1;
            if (binding < 0) return 0;
            if (SPACE(f, uses, nuses, cuses) < 0) return -1;
            Py_ssize_t id = f->nuses++;
            f->uses[id] = (Use){.binding = binding, .block = block, .position = pos,
                .query = source_query, .kind = use_kind, .expression = e};
            return add_event(f, block, 0, id);
        }
        case QueryExpr_kind: {
            expr_ty source = e->v.QueryExpr.source;
            if (walk_expr(f, source, block, pos, deferred,
                          source->kind == Name_kind ? SOURCE : READ, query_id(f, e)) < 0) return -1;
            for (Py_ssize_t i = 0; i < asdl_seq_LEN(e->v.QueryExpr.predicates); i++) {
                if (walk_expr(f, asdl_seq_GET(e->v.QueryExpr.predicates, i), block, pos, 1, READ, -1) < 0) return -1;
            }
            return walk_expr(f, e->v.QueryExpr.selector, block, pos, 1, READ, -1);
        }
        case Lambda_kind:
            SEQ(e->v.Lambda.args->defaults); SEQ(e->v.Lambda.args->kw_defaults);
            return walk_expr(f, e->v.Lambda.body, block, pos, 1, READ, -1);
        case BoolOp_kind: SEQ(e->v.BoolOp.values); break;
        case BinOp_kind: WALK(e->v.BinOp.left); WALK(e->v.BinOp.right); break;
        case UnaryOp_kind: WALK(e->v.UnaryOp.operand); break;
        case IfExp_kind: WALK(e->v.IfExp.test); WALK(e->v.IfExp.body); WALK(e->v.IfExp.orelse); break;
        case Dict_kind: SEQ(e->v.Dict.keys); SEQ(e->v.Dict.values); break;
        case Set_kind: SEQ(e->v.Set.elts); break;
        case List_kind: SEQ(e->v.List.elts); break;
        case Tuple_kind: SEQ(e->v.Tuple.elts); break;
        case Compare_kind: WALK(e->v.Compare.left); SEQ(e->v.Compare.comparators); break;
        case Call_kind:
            WALK(e->v.Call.func);
            for (Py_ssize_t i = 0; i < asdl_seq_LEN(e->v.Call.args); i++) {
                if (walk_expr(f, asdl_seq_GET(e->v.Call.args, i), block, pos, deferred, ESCAPE, -1) < 0) return -1;
            }
            for (Py_ssize_t i = 0; i < asdl_seq_LEN(e->v.Call.keywords); i++) {
                keyword_ty kw = asdl_seq_GET(e->v.Call.keywords, i);
                if (walk_expr(f, kw->value, block, pos, deferred, ESCAPE, -1) < 0) return -1;
            }
            break;
        case Attribute_kind:
            if (reflective_attribute(e->v.Attribute.attr) && hazard(f, "frame_access", SRC_LOCATION_FROM_AST(e)) < 0) return -1;
            WALK(e->v.Attribute.value); break;
        case Subscript_kind: WALK(e->v.Subscript.value); WALK(e->v.Subscript.slice); break;
        case Slice_kind: WALK(e->v.Slice.lower); WALK(e->v.Slice.upper); WALK(e->v.Slice.step); break;
        case Starred_kind: WALK(e->v.Starred.value); break;
        case FormattedValue_kind: WALK(e->v.FormattedValue.value); WALK(e->v.FormattedValue.format_spec); break;
        case Interpolation_kind: WALK(e->v.Interpolation.value); WALK(e->v.Interpolation.format_spec); break;
        case JoinedStr_kind: SEQ(e->v.JoinedStr.values); break;
        case TemplateStr_kind: SEQ(e->v.TemplateStr.values); break;
        case Constant_kind: break;
        case NamedExpr_kind:
            if (!deferred && hazard(f, "unsupported_binding_expression", SRC_LOCATION_FROM_AST(e)) < 0) return -1;
            WALK(e->v.NamedExpr.value); break;
        case Await_kind:
            if (!deferred && hazard(f, "unsupported_suspension", SRC_LOCATION_FROM_AST(e)) < 0) return -1;
            WALK(e->v.Await.value); break;
        case Yield_kind:
            if (!deferred && hazard(f, "unsupported_suspension", SRC_LOCATION_FROM_AST(e)) < 0) return -1;
            WALK(e->v.Yield.value); break;
        case YieldFrom_kind:
            if (!deferred && hazard(f, "unsupported_suspension", SRC_LOCATION_FROM_AST(e)) < 0) return -1;
            WALK(e->v.YieldFrom.value); break;
        case ListComp_kind: case SetComp_kind: case DictComp_kind: case GeneratorExp_kind: {
            if (!deferred && hazard(f, "unsupported_comprehension", SRC_LOCATION_FROM_AST(e)) < 0) return -1;
            asdl_comprehension_seq *generators;
            if (e->kind == DictComp_kind) {
                generators = e->v.DictComp.generators;
                WALK(e->v.DictComp.key); WALK(e->v.DictComp.value);
            }
            else if (e->kind == ListComp_kind) { generators = e->v.ListComp.generators; WALK(e->v.ListComp.elt); }
            else if (e->kind == SetComp_kind) { generators = e->v.SetComp.generators; WALK(e->v.SetComp.elt); }
            else { generators = e->v.GeneratorExp.generators; WALK(e->v.GeneratorExp.elt); }
            for (Py_ssize_t i = 0; i < asdl_seq_LEN(generators); i++) {
                comprehension_ty g = asdl_seq_GET(generators, i);
                WALK(g->iter); SEQ(g->ifs);
            }
            break;
        }
        default: return hazard(f, "unsupported_expression", SRC_LOCATION_FROM_AST(e));
    }
#undef WALK
#undef SEQ
    return 0;
}
static int
walk_expr(Function *f, expr_ty e, Py_ssize_t block, Py_ssize_t pos,
          int deferred, UseKind kind, Py_ssize_t query)
{
    if (e == NULL) return 0;
    if (Py_EnterRecursiveCall(" while analyzing a LINQ expression")) return -1;
    int result = walk_expr_impl(f, e, block, pos, deferred, kind, query);
    Py_LeaveRecursiveCall();
    return result;
}

static int
build_sequence(Function *f, asdl_stmt_seq *statements, Py_ssize_t *current)
{
    if (Py_EnterRecursiveCall(" while building a LINQ control-flow graph")) return -1;
    int result = -1;
    for (Py_ssize_t i = 0; i < asdl_seq_LEN(statements); i++) {
        stmt_ty s = asdl_seq_GET(statements, i);
        if (*current < 0) { *current = new_block(f); if (*current < 0) goto done; }
        Py_ssize_t block = *current;
        Block *b = &f->blocks[block];
        if (SPACE(b, statements, nstatements, cstatements) < 0) goto done;
        Py_ssize_t pos = b->nstatements;
        b->statements[b->nstatements++] = s;
        switch (s->kind) {
            case Assign_kind: {
                if (asdl_seq_LEN(s->v.Assign.targets) != 1 ||
                    ((expr_ty)asdl_seq_GET(s->v.Assign.targets, 0))->kind != Name_kind) {
                    if (hazard(f, "unsupported_assignment_target", SRC_LOCATION_FROM_AST(s)) < 0) goto done;
                    break;
                }
                expr_ty target = asdl_seq_GET(s->v.Assign.targets, 0), value = s->v.Assign.value;
                if (walk_expr(f, value, block, pos, 0, value->kind == Name_kind ? ALIAS : READ, -1) < 0) goto done;
                Py_ssize_t binding;
                if (binding_id(f, target->v.Name.id, &binding) < 0) goto done;
                if (binding < 0) {
                    if (hazard(f, "nonlocal_store", SRC_LOCATION_FROM_AST(target)) < 0) goto done;
                }
                else if (add_definition(f, binding, s, block, pos, value) < 0) goto done;
                break;
            }
            case Expr_kind:
                if (walk_expr(f, s->v.Expr.value, block, pos, 0, READ, -1) < 0) goto done;
                break;
            case Return_kind:
                if (walk_expr(f, s->v.Return.value, block, pos, 0, ESCAPE, -1) < 0 || edge(f, block, 1) < 0) goto done;
                *current = -1;
                break;
            case If_kind: {
                if (walk_expr(f, s->v.If.test, block, pos, 0, READ, -1) < 0) goto done;
                Py_ssize_t yes = new_block(f), no = new_block(f), join = new_block(f);
                if (yes < 0 || no < 0 || join < 0 || edge(f, block, yes) < 0 || edge(f, block, no) < 0) goto done;
                if (build_sequence(f, s->v.If.body, &yes) < 0 || build_sequence(f, s->v.If.orelse, &no) < 0) goto done;
                if ((yes >= 0 && edge(f, yes, join) < 0) || (no >= 0 && edge(f, no, join) < 0)) goto done;
                *current = join;
                break;
            }
            case Pass_kind: case Global_kind: case Nonlocal_kind: break;
            default:
                if (hazard(f, "unsupported_statement", SRC_LOCATION_FROM_AST(s)) < 0) goto done;
                break;
        }
    }
    result = 0;
done:
    Py_LeaveRecursiveCall();
    return result;
}

static int
solve(Function *f)
{
    Py_ssize_t count = f->nblocks;
    Py_ssize_t *queue = PyMem_Calloc(count, sizeof(Py_ssize_t));
    unsigned char *queued = PyMem_Calloc(count, 1);
    Bits boundary = {0}, temp = {0}, out = {0}, dom = {0}, reachable = {0};
    int result = -1;
    if (queue == NULL || queued == NULL) { PyErr_NoMemory(); goto done; }
    if (bits_new(&boundary, f->ndefinitions) < 0 || bits_new(&temp, f->ndefinitions) < 0 ||
        bits_new(&out, f->ndefinitions) < 0 || bits_new(&dom, count) < 0 || bits_new(&reachable, count) < 0) goto done;
    for (Py_ssize_t i = 0; i < f->nbindings; i++) if (bits_new(&f->bindings[i].definitions, f->ndefinitions) < 0) goto done;
    for (Py_ssize_t i = 0; i < f->ndefinitions; i++) {
        Definition *d = &f->definitions[i];
        bits_add(f->bindings[d->binding].definitions, i);
        if (d->kind != ASSIGNMENT) bits_add(boundary, i);
    }
    Py_ssize_t head = 0, tail = 1;
    queue[0] = 0; f->blocks[0].reachable = 1;
    while (head < tail) {
        Block *b = &f->blocks[queue[head++]];
        for (Py_ssize_t j = 0; j < b->nsuccessors; j++) {
            Py_ssize_t next = b->successors[j];
            if (!f->blocks[next].reachable) { f->blocks[next].reachable = 1; queue[tail++] = next; }
        }
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        Block *b = &f->blocks[i];
        if (bits_new(&b->gen, f->ndefinitions) < 0 || bits_new(&b->kill, f->ndefinitions) < 0 ||
            bits_new(&b->in, f->ndefinitions) < 0 || bits_new(&b->out, f->ndefinitions) < 0 || bits_new(&b->dominators, count) < 0) goto done;
        if (b->reachable) bits_add(reachable, i);
        for (Py_ssize_t j = 0; j < b->nevents; j++) {
            Event *event = &b->events[j];
            if (!event->is_definition) continue;
            Definition *d = &f->definitions[event->index];
            Bits mask = f->bindings[d->binding].definitions;
            bits_union(b->kill, mask); bits_remove(b->gen, mask); bits_add(b->gen, event->index);
        }
        bits_remove(b->kill, b->gen);
    }
    head = 0; Py_ssize_t length = 0;
    for (Py_ssize_t i = 0; i < count; i++) if (f->blocks[i].reachable) { queue[length++] = i; queued[i] = 1; }
    while (length) {
        Py_ssize_t id = queue[head]; head = (head + 1) % count; length--; queued[id] = 0;
        Block *b = &f->blocks[id]; bits_zero(temp);
        if (id == 0) bits_copy(temp, boundary);
        else for (Py_ssize_t i = 0; i < b->npredecessors; i++) bits_union(temp, f->blocks[b->predecessors[i]].out);
        bits_copy(b->in, temp); bits_remove(temp, b->kill); bits_union(temp, b->gen);
        if (bits_equal(temp, b->out)) continue;
        bits_copy(b->out, temp);
        for (Py_ssize_t i = 0; i < b->nsuccessors; i++) {
            Py_ssize_t next = b->successors[i];
            if (!queued[next]) { queue[(head + length) % count] = next; length++; queued[next] = 1; }
        }
    }
    for (Py_ssize_t i = 0; i < count; i++) {
        if (!f->blocks[i].reachable) continue;
        if (i == 0) bits_add(f->blocks[i].dominators, 0);
        else bits_copy(f->blocks[i].dominators, reachable);
    }
    int changed;
    do {
        changed = 0;
        for (Py_ssize_t i = 1; i < count; i++) {
            Block *b = &f->blocks[i]; if (!b->reachable) continue;
            bits_copy(dom, reachable);
            for (Py_ssize_t j = 0; j < b->npredecessors; j++) {
                Block *p = &f->blocks[b->predecessors[j]];
                if (p->reachable) bits_intersect(dom, p->dominators);
            }
            bits_add(dom, i);
            if (!bits_equal(dom, b->dominators)) { bits_copy(b->dominators, dom); changed = 1; }
        }
    } while (changed);
    for (Py_ssize_t i = 0; i < count; i++) {
        Block *b = &f->blocks[i]; bits_copy(out, b->in);
        for (Py_ssize_t j = 0; j < b->nevents; j++) {
            Event *event = &b->events[j];
            if (event->is_definition) {
                Definition *d = &f->definitions[event->index];
                bits_remove(out, f->bindings[d->binding].definitions); bits_add(out, event->index);
            }
            else {
                Use *u = &f->uses[event->index];
                if (bits_new(&u->reaching, f->ndefinitions) < 0) goto done;
                if (b->reachable) { bits_copy(u->reaching, out); bits_intersect(u->reaching, f->bindings[u->binding].definitions); }
            }
        }
    }
    result = 0;
done:
    PyMem_Free(queue); PyMem_Free(queued);
    bits_free(&boundary); bits_free(&temp); bits_free(&out); bits_free(&dom); bits_free(&reachable);
    return result;
}

static int
analyze_function(Function *f)
{
    if (f->node->kind == AsyncFunctionDef_kind && hazard(f, "async_function", SRC_LOCATION_FROM_AST(f->node)) < 0) return -1;
    if (f->scope->ste_generator && hazard(f, "generator_function", SRC_LOCATION_FROM_AST(f->node)) < 0) return -1;
    if (init_bindings(f) < 0 || new_block(f) < 0 || new_block(f) < 0) return -1;
    Py_ssize_t current = 0;
    asdl_stmt_seq *body = f->node->kind == FunctionDef_kind ? f->node->v.FunctionDef.body : f->node->v.AsyncFunctionDef.body;
    if (build_sequence(f, body, &current) < 0 || (current >= 0 && edge(f, current, 1) < 0)) return -1;
    return f->nhazards ? 0 : solve(f);
}

static int
decide(Function *f, Py_ssize_t query, _PyLinqDecision *decision)
{
    if (f->nhazards) { decision->reason = "unsupported_function"; return 0; }
    decision->status = _PyLinq_INELIGIBLE;
    Py_ssize_t consumer = -1;
    for (Py_ssize_t i = 0; i < f->ndefinitions; i++) if (f->definitions[i].query == query) { consumer = i; break; }
    if (consumer < 0) { decision->reason = "not_direct_query_assignment"; return 0; }
    Definition *c = &f->definitions[consumer];
    if (!f->blocks[c->block].reachable) { decision->reason = "unreachable"; return 0; }
    _PyLinqPlan *plan = &f->plans->plans[query];
    if (plan->operators[0].expression->kind != Name_kind) { decision->reason = "not_named_source"; return 0; }
    Py_ssize_t source;
    if (binding_id(f, plan->operators[0].expression->v.Name.id, &source) < 0) return -1;
    if (source < 0) { decision->reason = "nonlocal_source_binding"; return 0; }
    Py_ssize_t use = -1;
    for (Py_ssize_t i = 0; i < f->nuses; i++) if (f->uses[i].kind == SOURCE && f->uses[i].query == query) { use = i; break; }
    if (use < 0) { PyErr_SetString(PyExc_SystemError, "missing LINQ source use"); return -1; }
    decision->source_use = use;
    Py_ssize_t producer = -1, reaching = 0;
    for (Py_ssize_t i = 0; i < f->ndefinitions; i++) {
        if (!bits_has(f->uses[use].reaching, i)) continue;
        if (f->definitions[i].kind == UNBOUND) { decision->reason = "possibly_unbound"; return 0; }
        reaching++; producer = i;
    }
    if (reaching != 1) { decision->reason = "ambiguous_producer"; return 0; }
    Definition *p = &f->definitions[producer];
    if (p->query < 0) { decision->reason = "not_query_definition"; return 0; }
    decision->producer_definition = producer; decision->producer_plan = p->query;
    if (p->binding == c->binding) { decision->reason = "same_binding"; return 0; }
    if (_PyST_GetScope(f->scope, f->bindings[source].name) == CELL) { decision->reason = "producer_captured"; return 0; }
    if (!bits_has(f->blocks[c->block].dominators, p->block) ||
        (p->block == c->block && p->position >= c->position)) { decision->reason = "producer_not_dominating"; return 0; }
    if (!f->plans->plans[p->query].identity_projection) { decision->reason = "producer_projection"; return 0; }
    if (!plan->identity_projection) { decision->reason = "consumer_projection"; return 0; }
    if (plan->operator_count == 2) { decision->reason = "consumer_without_filter"; return 0; }
    for (Py_ssize_t i = 0; i < f->nuses; i++) {
        if (i != use && bits_has(f->uses[i].reaching, producer)) {
            decision->extra_use = i;
            decision->reason = f->uses[i].kind == ALIAS ? "producer_aliased" : "producer_extra_use";
            return 0;
        }
    }
    if (p->block != c->block || p->position + 1 != c->position) { decision->reason = "not_adjacent"; return 0; }
    decision->status = _PyLinq_ELIGIBLE; decision->reason = "eligible";
    return 0;
}

int
_PyLinq_Analyze(struct symtable *st, _PyLinqPlans *plans)
{
    _PyLinqAnalysis *a = PyMem_Calloc(1, sizeof(*a));
    if (a == NULL) { PyErr_NoMemory(); return -1; }
    plans->analysis = a;
    if (plans->count == 0) return 0;
    a->decisions = PyMem_Calloc(plans->count, sizeof(_PyLinqDecision));
    if (a->decisions == NULL) { PyErr_NoMemory(); return -1; }
    for (Py_ssize_t i = 0; i < plans->count; i++) a->decisions[i] = (_PyLinqDecision){
        .status = _PyLinq_UNKNOWN, .reason = "unsupported_scope", .function = -1,
        .source_use = -1, .producer_definition = -1, .producer_plan = -1, .extra_use = -1};
    Py_ssize_t capacity = st->st_linq_functions ? PyDict_GET_SIZE(st->st_linq_functions) : 0;
    if (capacity == 0) return 0;
    a->functions = PyMem_Calloc(capacity, sizeof(Function));
    if (a->functions == NULL) { PyErr_NoMemory(); return -1; }
    Py_ssize_t pos = 0; PyObject *key, *context;
    while (PyDict_Next(st->st_linq_functions, &pos, &key, &context)) {
        PySTEntryObject *scope = (PySTEntryObject *)PyTuple_GET_ITEM(context, 0);
        int relevant = 0;
        for (Py_ssize_t i = 0; i < plans->count; i++) if (plans->plans[i].owner == scope) relevant = 1;
        if (!relevant) continue;
        Py_ssize_t id = a->count++;
        Function *f = &a->functions[id];
        f->node = PyLong_AsVoidPtr(key); f->scope = scope; f->plans = plans;
        if (f->node == NULL && PyErr_Occurred()) return -1;
        PyObject *private = PyTuple_GET_ITEM(context, 1);
        f->private_name = private == Py_None ? NULL : private;
        if (analyze_function(f) < 0) return -1;
        for (Py_ssize_t i = 0; i < plans->count; i++) if (plans->plans[i].owner == scope) {
            a->decisions[i].function = id;
            if (decide(f, i, &a->decisions[i]) < 0) return -1;
        }
    }
    return 0;
}

const _PyLinqDecision *
_PyLinq_Decision(_PyLinqPlans *plans, expr_ty query)
{
    const _PyLinqPlan *plan = _PyLinq_Lookup(plans, query);
    return plan ? &plans->analysis->decisions[plan - plans->plans] : NULL;
}

/* Serialization is separate from the compiler's native analysis state. */
static int
put(PyObject *dict, const char *key, PyObject *value)
{
    if (value == NULL) return -1;
    int result = PyDict_SetItemString(dict, key, value);
    Py_DECREF(value);
    return result;
}
static PyObject *number(Py_ssize_t id) { return id < 0 ? Py_NewRef(Py_None) : PyLong_FromSsize_t(id); }
static PyObject *location(_Py_SourceLocation loc) { return Py_BuildValue("(iiii)", loc.lineno, loc.col_offset, loc.end_lineno, loc.end_col_offset); }
static const char *status_name(_PyLinqStatus s) { return s == _PyLinq_ELIGIBLE ? "eligible" : s == _PyLinq_INELIGIBLE ? "ineligible" : "unknown"; }
static PyObject *
bit_list(Bits bits)
{
    PyObject *list = PyList_New(0);
    if (list == NULL) return NULL;
    for (Py_ssize_t i = 0; i < bits.count * 64; i++) if (bits_has(bits, i)) {
        PyObject *item = PyLong_FromSsize_t(i);
        if (item == NULL) { Py_DECREF(list); return NULL; }
        int result = PyList_Append(list, item); Py_DECREF(item);
        if (result < 0) { Py_DECREF(list); return NULL; }
    }
    return list;
}
static PyObject *
number_list(Py_ssize_t *values, Py_ssize_t count)
{
    PyObject *list = PyList_New(count);
    if (list == NULL) return NULL;
    for (Py_ssize_t i = 0; i < count; i++) {
        PyObject *item = PyLong_FromSsize_t(values[i]);
        if (item == NULL) { Py_DECREF(list); return NULL; }
        PyList_SET_ITEM(list, i, item);
    }
    if (PyList_Sort(list) < 0) { Py_DECREF(list); return NULL; }
    return list;
}

static PyObject *
describe_blocks(Function *f)
{
    PyObject *blocks = PyList_New(f->nblocks);
    if (blocks == NULL) return NULL;
    for (Py_ssize_t i = 0; i < f->nblocks; i++) {
        Block *b = &f->blocks[i];
        PyObject *item = Py_BuildValue("{s:n,s:O}", "id", i, "reachable", b->reachable ? Py_True : Py_False);
        PyObject *statements = PyList_New(b->nstatements);
        if (item == NULL || statements == NULL) { Py_XDECREF(item); Py_XDECREF(statements); goto error; }
        for (Py_ssize_t j = 0; j < b->nstatements; j++) {
            PyObject *loc = location(SRC_LOCATION_FROM_AST(b->statements[j]));
            if (loc == NULL) { Py_DECREF(item); Py_DECREF(statements); goto error; }
            PyList_SET_ITEM(statements, j, loc);
        }
        if (put(item, "statements", statements) < 0 ||
            put(item, "predecessors", number_list(b->predecessors, b->npredecessors)) < 0 ||
            put(item, "successors", number_list(b->successors, b->nsuccessors)) < 0 ||
            put(item, "gen", bit_list(b->gen)) < 0 || put(item, "kill", bit_list(b->kill)) < 0 ||
            put(item, "in", bit_list(b->in)) < 0 || put(item, "out", bit_list(b->out)) < 0 ||
            put(item, "dominators", bit_list(b->dominators)) < 0) { Py_DECREF(item); goto error; }
        PyList_SET_ITEM(blocks, i, item);
    }
    return blocks;
error:
    Py_DECREF(blocks); return NULL;
}

static PyObject *
describe_bindings(Function *f)
{
    PyObject *items = PyList_New(f->nbindings);
    if (items == NULL) return NULL;
    for (Py_ssize_t i = 0; i < f->nbindings; i++) {
        Binding *b = &f->bindings[i];
        PyObject *item = Py_BuildValue("{s:n,s:O,s:O}", "id", i, "name", b->name,
            "captured", _PyST_GetScope(f->scope, b->name) == CELL ? Py_True : Py_False);
        if (item == NULL || put(item, "definitions", bit_list(b->definitions)) < 0) {
            Py_XDECREF(item); Py_DECREF(items); return NULL;
        }
        PyList_SET_ITEM(items, i, item);
    }
    return items;
}

static PyObject *
describe_definitions(Function *f)
{
    PyObject *items = PyList_New(f->ndefinitions);
    if (items == NULL) return NULL;
    for (Py_ssize_t i = 0; i < f->ndefinitions; i++) {
        Definition *d = &f->definitions[i];
        PyObject *item = Py_BuildValue("{s:n,s:n,s:s}", "id", i, "binding", d->binding,
            "kind", d->kind == PARAMETER ? "parameter" : d->kind == UNBOUND ? "unbound" : "assignment");
        _Py_SourceLocation loc = d->statement ? SRC_LOCATION_FROM_AST(d->statement) : SRC_LOCATION_FROM_AST(f->node);
        if (item == NULL || put(item, "block", number(d->block)) < 0 ||
            put(item, "position", number(d->position)) < 0 || put(item, "query", number(d->query)) < 0 ||
            put(item, "location", location(loc)) < 0) { Py_XDECREF(item); Py_DECREF(items); return NULL; }
        PyList_SET_ITEM(items, i, item);
    }
    return items;
}

static PyObject *
describe_uses(Function *f)
{
    PyObject *items = PyList_New(f->nuses);
    if (items == NULL) return NULL;
    for (Py_ssize_t i = 0; i < f->nuses; i++) {
        Use *u = &f->uses[i];
        const char *kind = u->kind == SOURCE ? "query_source" : u->kind == ALIAS ? "alias" : u->kind == ESCAPE ? "escape" : "read";
        PyObject *item = Py_BuildValue("{s:n,s:n,s:n,s:n,s:s}", "id", i, "binding", u->binding,
                                      "block", u->block, "position", u->position, "kind", kind);
        if (item == NULL || put(item, "query", number(u->query)) < 0 ||
            put(item, "location", location(SRC_LOCATION_FROM_AST(u->expression))) < 0 ||
            put(item, "reaching_definitions", bit_list(u->reaching)) < 0) { Py_XDECREF(item); Py_DECREF(items); return NULL; }
        PyList_SET_ITEM(items, i, item);
    }
    return items;
}

static PyObject *
describe_function(Function *f, Py_ssize_t id)
{
    PyObject *item = Py_BuildValue("{s:n,s:O,s:s}", "id", id, "name", f->scope->ste_name,
                                  "status", f->nhazards ? "unknown" : "analyzed");
    if (item == NULL) return NULL;
    PyObject *reasons = PyList_New(f->nhazards);
    if (reasons == NULL) goto error;
    for (Py_ssize_t i = 0; i < f->nhazards; i++) {
        PyObject *reason = Py_BuildValue("{s:s}", "code", f->hazards[i].code);
        if (reason == NULL || put(reason, "location", location(f->hazards[i].location)) < 0) {
            Py_XDECREF(reason); Py_DECREF(reasons); goto error;
        }
        PyList_SET_ITEM(reasons, i, reason);
    }
    if (put(item, "reasons", reasons) < 0 || put(item, "location", location(SRC_LOCATION_FROM_AST(f->node))) < 0) goto error;
    if (!f->nhazards && (put(item, "blocks", describe_blocks(f)) < 0 ||
        put(item, "bindings", describe_bindings(f)) < 0 || put(item, "definitions", describe_definitions(f)) < 0 ||
        put(item, "uses", describe_uses(f)) < 0 || put(item, "entry", PyLong_FromLong(0)) < 0 ||
        put(item, "exit", PyLong_FromLong(1)) < 0)) goto error;
    return item;
error:
    Py_DECREF(item); return NULL;
}

PyObject *
_PyLinq_AnalysisDescribe(const _PyLinqPlans *plans)
{
    _PyLinqAnalysis *a = plans->analysis;
    PyObject *functions = PyList_New(a->count), *candidates = PyList_New(plans->count);
    PyObject *result = NULL;
    if (functions == NULL || candidates == NULL) goto done;
    for (Py_ssize_t i = 0; i < a->count; i++) {
        PyObject *function = describe_function(&a->functions[i], i);
        if (function == NULL) goto done;
        PyList_SET_ITEM(functions, i, function);
    }
    for (Py_ssize_t i = 0; i < plans->count; i++) {
        _PyLinqDecision *d = &a->decisions[i];
        PyObject *item = Py_BuildValue("{s:n,s:s,s:s}", "consumer", i,
                                      "status", status_name(d->status), "reason", d->reason);
        if (item == NULL) goto done;
        if (put(item, "location", location(SRC_LOCATION_FROM_AST(plans->plans[i].query))) < 0 ||
            put(item, "function", number(d->function)) < 0 || put(item, "source_use", number(d->source_use)) < 0 ||
            put(item, "producer_definition", number(d->producer_definition)) < 0 ||
            put(item, "producer_plan", number(d->producer_plan)) < 0 || put(item, "extra_use", number(d->extra_use)) < 0) { Py_DECREF(item); goto done; }
        if (d->function >= 0) {
            Function *f = &a->functions[d->function];
            if (d->source_use >= 0 && put(item, "reaching_definitions", bit_list(f->uses[d->source_use].reaching)) < 0) { Py_DECREF(item); goto done; }
            if (d->producer_definition >= 0) {
                Definition *p = &f->definitions[d->producer_definition];
                Use *u = &f->uses[d->source_use];
                int dominates = bits_has(f->blocks[u->block].dominators, p->block) &&
                                (p->block != u->block || p->position < u->position);
                int adjacent = 0;
                for (Py_ssize_t j = 0; j < f->ndefinitions; j++) {
                    Definition *c = &f->definitions[j];
                    if (c->query == i) adjacent = p->block == c->block && p->position + 1 == c->position;
                }
                if (put(item, "dominates", PyBool_FromLong(dominates)) < 0 ||
                    put(item, "adjacent", PyBool_FromLong(adjacent)) < 0) { Py_DECREF(item); goto done; }
            }
        }
        PyList_SET_ITEM(candidates, i, item);
    }
    result = Py_BuildValue("{s:O,s:O}", "functions", functions, "candidates", candidates);
done:
    Py_XDECREF(functions); Py_XDECREF(candidates); return result;
}
