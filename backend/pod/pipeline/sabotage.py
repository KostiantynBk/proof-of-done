"""S7–S8: two independent proposals and static mutation validation."""
import ast
import asyncio
import hashlib
import re
import sys
from collections.abc import Callable

from pod.pipeline.baseline import require_tree, validate_items
from pod.pipeline.context import RunContext
from pod.pipeline.prompts_prove import saboteur_messages
from pod.pipeline.render import protected_test_files
from pod.schemas import Item, Mutation, MutationOrigin, MutationProposal, MutationSet
from pod.verify.snapshot import Snapshot


def judge_relevance(ctx: RunContext, item: Item, mutation: Mutation) -> float | None:
    return None


def _behavior_tree(source: str) -> ast.AST:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:]
    return tree


_FORBIDDEN = ('pytest', 'unittest', '_pytest', 'subprocess', 'socket', 'builtins',
              'setattr', 'getattr', 'globals', 'locals', 'exec', 'eval', 'open', 'exit',
              '__', 'import', 'compile', 'atexit', 'monkeypatch', 'trace', 'popen',
              'spawn', 'system', 'shutil', 'requests', 'urllib', 'write', 'unlink',
              'remove', 'rmdir', 'delete', 'chmod', 'rename', 'mkdir', 'breakpoint')
_SAFE_REFS = {'float', 'int', 'str', 'round', 'abs', 'print', 'ValueError', 'TypeError',
              'KeyError', 'RuntimeError', 'Exception'}


def _same(a, b) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, ast.AST) and isinstance(b, ast.AST):
        return ast.dump(a, include_attributes=False) == ast.dump(b, include_attributes=False)
    return a == b


def _ref(node) -> str | None:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
        base = _ref(node.value)
        return f'{base}.{node.attr}' if base else None
    return None


def _unsafe(name: str) -> bool:
    text = name.lower()
    return (any(part in text for part in _FORBIDDEN) or 'os.' in text or 'sys.' in text
            or any(part in {'os', 'sys'} for part in text.split('.')))


def _safe_subtree(node: ast.AST) -> bool:
    return not any(isinstance(n, (ast.Import, ast.ImportFrom, ast.Assert)) or
                   (_ref(n) is not None and _unsafe(_ref(n))) for n in ast.walk(node))


def _bound_in_scope(scope: ast.AST) -> set[str]:
    """Collect local bindings without crossing a nested definition."""
    names: set[str] = set()

    class Collector(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Param)):
                names.add(node.id)

        def visit_arg(self, node: ast.arg) -> None:
            names.add(node.arg)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is scope:
                self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            if node is scope:
                self.generic_visit(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

    Collector().visit(scope)
    return names


def _expression_checker(tree: ast.AST, source: str, find: str, refs: set[str],
                        imports: set[str]) -> Callable[[ast.AST, ast.AST], bool]:
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def allowed(old: ast.AST, new: ast.AST) -> bool:
        if not isinstance(old, ast.expr) or not isinstance(new, ast.expr):
            return False
        # The proposed find must identify this complete expression, not hide multiple
        # independent edits in a larger statement or block.
        segment = ast.get_source_segment(source, old)
        if not segment or not _safe_subtree(old):
            return False
        # A name may be anchored by its containing statement for uniqueness; AST
        # comparison still counts every separately changed sibling as another edit.
        if segment != find and not (isinstance(old, ast.Name) and segment in find):
            return False
        if isinstance(old, (ast.Name, ast.Attribute)) and not isinstance(old.ctx, ast.Load):
            return False
        nodes = list(ast.walk(new))
        if len(nodes) > 12:
            return False
        permitted = (ast.Constant, ast.Name, ast.Attribute, ast.Call, ast.BinOp, ast.BoolOp,
                     ast.UnaryOp, ast.Compare, ast.Load, ast.operator, ast.boolop,
                     ast.unaryop, ast.cmpop, ast.keyword)
        if any(not isinstance(node, permitted) for node in nodes):
            return False
        scope = parents.get(old)
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            scope = parents.get(scope)
        bound = imports | (_bound_in_scope(scope) if scope is not None else set())
        for node in nodes:
            if isinstance(node, ast.Constant):
                if type(node.value) not in (int, float, bool, str, type(None)):
                    return False
                if isinstance(node.value, str) and _unsafe(node.value):
                    return False
            elif isinstance(node, ast.keyword) and (node.arg is None or _unsafe(node.arg)):
                return False
            elif isinstance(node, (ast.Name, ast.Attribute)):
                ref = _ref(node)
                if ref is None or _unsafe(ref):
                    return False
                base = ref.split('.')[0]
                if base in sys.stdlib_module_names and base not in imports and base not in bound:
                    return False
                if isinstance(node, ast.Attribute):
                    if ref not in refs and base not in bound:
                        return False
                elif ref not in refs and ref not in bound:
                    return False
        return True

    return allowed


def _changes(old, new, refs: set[str], *, in_function: bool = False,
             parent: ast.AST | None = None, field: str = '',
             expression_allowed: Callable[[ast.AST, ast.AST], bool] | None = None) -> int | None:
    """Accept one bounded AST edit; never execute source or replacement."""
    if _same(old, new):
        return 0
    if expression_allowed and expression_allowed(old, new):
        return 1
    if isinstance(old, list) and isinstance(new, list):
        # Remove one argument or one safe statement from a function body.
        if len(old) == len(new) + 1:
            for i, removed in enumerate(old):
                if all(_same(a, b) for a, b in zip(old[:i] + old[i + 1:], new, strict=True)):
                    if isinstance(parent, ast.Call) and field in ('args', 'keywords'):
                        return 1 if _safe_subtree(removed) else None
                    simple = isinstance(removed, (ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign,
                                                  ast.Raise, ast.Return))
                    guard = (isinstance(removed, ast.If) and not removed.orelse and removed.body
                             and all(isinstance(s, (ast.Raise, ast.Return)) for s in removed.body))
                    return 1 if in_function and field in ('body', 'orelse', 'finalbody') and (simple or guard) and _safe_subtree(removed) else None
            return None
        if len(old) != len(new):
            return None
        values = [_changes(a, b, refs, in_function=in_function, parent=parent, field=field,
                           expression_allowed=expression_allowed)
                  for a, b in zip(old, new, strict=True)]
        return None if any(v is None for v in values) else sum(values)
    if not isinstance(old, ast.AST) or not isinstance(new, ast.AST):
        return None
    if in_function and isinstance(new, ast.Pass) and isinstance(old, (
            ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Raise, ast.Return)):
        return 1 if _safe_subtree(old) else None
    if isinstance(old, ast.Assert) or isinstance(new, ast.Assert):
        return None
    if isinstance(old, (ast.Import, ast.ImportFrom)) or isinstance(new, (ast.Import, ast.ImportFrom)):
        return None
    if (in_function and isinstance(old, ast.AugAssign) and isinstance(new, ast.Assign)
            and len(new.targets) == 1 and _same(old.target, new.targets[0])
            and _same(old.value, new.value) and _safe_subtree(old)):
        return 1
    if isinstance(old, ast.BoolOp) and _safe_subtree(old):
        remaining = new.values if isinstance(new, ast.BoolOp) and type(old.op) is type(new.op) else [new]
        if len(old.values) == len(remaining) + 1 and any(
                all(_same(a, b) for a, b in zip(old.values[:i] + old.values[i + 1:], remaining, strict=True))
                for i in range(len(old.values))):
            return 1
    if ((isinstance(parent, (ast.If, ast.While, ast.IfExp)) and field == 'test'
         or isinstance(parent, (ast.BoolOp, ast.Compare)) and field in ('values', 'left', 'comparators'))
            and isinstance(new, ast.Constant) and type(new.value) is bool and _safe_subtree(old)):
        return 1
    if isinstance(new, ast.UnaryOp) and isinstance(new.op, ast.Not) and _same(old, new.operand):
        return 1 if _safe_subtree(old) else None
    if isinstance(old, ast.UnaryOp) and isinstance(old.op, ast.Not) and _same(old.operand, new):
        return 1 if _safe_subtree(new) else None
    operators = (ast.cmpop, ast.operator, ast.boolop, ast.unaryop)
    if isinstance(old, operators) and isinstance(new, operators):
        return 1 if type(old).__bases__ == type(new).__bases__ else None
    if (isinstance(parent, ast.Call) and field == 'args' and isinstance(old, ast.Name)
            and isinstance(old.ctx, ast.Load) and isinstance(new, ast.Constant)
            and type(new.value) in (str, int, float, bool, type(None))):
        return 1 if not _unsafe(old.id) and (not isinstance(new.value, str) or not _unsafe(new.value)) else None
    if isinstance(old, ast.Constant) and isinstance(new, ast.Constant):
        kind = type(old.value)
        if kind is type(new.value) and kind in (int, float, bool, str, type(None)):
            return 1 if not isinstance(new.value, str) or not _unsafe(new.value) else None
        return None
    old_ref, new_ref = _ref(old), _ref(new)
    if old_ref is not None and new_ref is not None:
        return 1 if new_ref in refs and not _unsafe(new_ref) and not _unsafe(old_ref) else None
    if type(old) is not type(new):
        return None
    # Do not add/change definitions or their decorators/signatures, or change assertions.
    if isinstance(old, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        for f in old._fields:
            if f != 'body' and not _same(getattr(old, f), getattr(new, f)):
                return None
        nested = isinstance(old, (ast.FunctionDef, ast.AsyncFunctionDef))
        return _changes(old.body, new.body, refs, in_function=nested, parent=old, field='body',
                        expression_allowed=expression_allowed)
    if isinstance(old, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return None
    if isinstance(old, ast.Call) and (_unsafe(ast.unparse(old.func)) or _unsafe(ast.unparse(new.func))):
        return None
    values = [_changes(getattr(old, f), getattr(new, f), refs, in_function=in_function,
                       parent=old, field=f, expression_allowed=expression_allowed) for f in old._fields]
    return None if any(v is None for v in values) else sum(values)


def validate_mutation(snapshot: Snapshot, mutation: Mutation, seen: set[tuple[str, str, str]]) -> Mutation:
    path = snapshot.safe_path(mutation.file)
    reason = None
    line = None
    key = None
    if path is None:
        reason = 'invalid_path'
    elif mutation.file.replace('\\', '/') in protected_test_files(snapshot):
        reason = 'test_file'
    elif mutation.find == mutation.replace:
        reason = 'unchanged'
    else:
        source = path.read_text(encoding='utf-8')
        count = source.count(mutation.find) if mutation.find else 0
        if count != 1:
            reason = 'find_not_unique' if count > 1 else 'find_not_found'
        else:
            line = source.count('\n', 0, source.index(mutation.find)) + 1
            changed = source.replace(mutation.find, mutation.replace, 1)
            try:
                original_tree = _behavior_tree(source)
                try:
                    mutated_tree = _behavior_tree(changed)
                except (SyntaxError, IndentationError):
                    start = source.index(mutation.find)
                    whole_lines = ((start == 0 or source[start - 1] == '\n')
                                   and (mutation.find.endswith('\n')
                                        or start + len(mutation.find) == len(source)))
                    first_line = next((line for line in mutation.find.splitlines() if line.strip()), '')
                    if mutation.replace.strip() or not whole_lines or not first_line:
                        raise
                    indent = re.match(r'[ \t]*', first_line).group()
                    normalized = indent + 'pass' + ('\n' if mutation.find.endswith('\n') else '')
                    changed = source.replace(mutation.find, normalized, 1)
                    mutated_tree = _behavior_tree(changed)
                    mutation = mutation.model_copy(update={
                        'replace': normalized,
                        'description': mutation.description + ' [normalized_to_pass]',
                    })
                imports = {alias.asname or alias.name.split('.')[0]
                           for node in ast.walk(original_tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                           for alias in node.names}
                refs = {ref for node in ast.walk(original_tree) if (ref := _ref(node))
                        and ('.' not in ref or ref.split('.')[0] not in sys.stdlib_module_names
                             or ref.split('.')[0] in imports)}
                refs.update(_SAFE_REFS | imports)
                check_expression = _expression_checker(original_tree, source, mutation.find, refs, imports)
                delta = _changes(original_tree, mutated_tree, refs, expression_allowed=check_expression)
                fingerprint = hashlib.sha256(ast.dump(mutated_tree, include_attributes=False).encode()).hexdigest()
                key = (path.relative_to(snapshot.root).as_posix(), fingerprint, '')
                if delta == 0:
                    reason = 'comment_or_whitespace_only'
                elif delta != 1:
                    reason = 'harness_tampering_or_unsupported_edit'
                elif key in seen:
                    reason = 'duplicate'
                else:
                    seen.add(key)
            except (SyntaxError, ValueError, IndentationError):
                reason = 'syntax_error'
    return mutation.model_copy(update={'line': line, 'valid': reason is None,
                                       'reject_reason': reason})


async def sabotage_items(ctx: RunContext) -> None:
    snapshot = Snapshot(ctx.project_dir)
    proposed: dict[tuple[str, str], list[MutationProposal]] = {}
    semaphore = asyncio.Semaphore(4)

    async def generate(item: Item, origin: MutationOrigin,
                       reasons: list[str] | None = None) -> list[MutationProposal]:
        try:
            async with semaphore:
                result = await ctx.llm_call('saboteur', saboteur_messages(
                    item, snapshot, blind=origin == 'blind', retry_reasons=reasons),
                    MutationSet, temperature=0.4)
            if result.value.item_id != item.id:
                raise ValueError('saboteur response item_id mismatch')
            if len(result.value.mutations) != 3:
                ctx.state.errors.append(f'sabotage {item.id} {origin}: expected 3 proposals, '
                                        f'got {len(result.value.mutations)}')
            return result.value.mutations
        except Exception as exc:  # noqa: BLE001 -- isolate untrusted calls
            ctx.state.errors.append(f'sabotage {item.id} {origin}: {type(exc).__name__}: {exc}'[:500])
            return []

    async with ctx.stage('S7', 'sabotage'):
        validate_items(ctx)
        require_tree(ctx, snapshot)
        ctx.state.mutations.clear()
        ctx.state.results.clear()
        ctx.state.verdicts.clear()
        eligible = [item for item in ctx.state.items if item.source_verified
                    and (item.kind != 'rule' or item.accepted)
                    and item.id in ctx.state.evidence
                    and ctx.state.evidence[item.id].has_verified_code]
        tasks = [(item, origin) for item in eligible for origin in ('targeted', 'blind')]
        responses = await asyncio.gather(*(generate(item, origin) for item, origin in tasks))
        proposed.update({(item.id, origin): response
                         for (item, origin), response in zip(tasks, responses, strict=True)})
    async with ctx.stage('S8', 'validate mutations'):
        require_tree(ctx, Snapshot(ctx.project_dir))
        seen_by_item: dict[str, set[tuple[str, str, str]]] = {item.id: set() for item in eligible}
        for item in eligible:
            ctx.state.mutations[item.id] = []

        def accept(item: Item, origin: MutationOrigin, batch: list[MutationProposal]) -> None:
            mutations = ctx.state.mutations[item.id]
            prefix = 'T' if origin == 'targeted' else 'B'
            for proposal in batch:
                number = sum(m.origin == origin for m in mutations) + 1
                mutation = Mutation(id=f'{item.id}-{prefix}{number}', item_id=item.id,
                                    origin=origin, **proposal.model_dump())
                if sum(m.valid and m.origin == origin for m in mutations) >= 3:
                    mutation = mutation.model_copy(update={'reject_reason': 'excess_proposal'})
                else:
                    mutation = validate_mutation(snapshot, mutation, seen_by_item[item.id])
                mutations.append(mutation)

        for item, origin in tasks:
            accept(item, origin, proposed.get((item.id, origin), []))
        retries = []
        for item, origin in tasks:
            previous = [m for m in ctx.state.mutations[item.id] if m.origin == origin]
            if sum(m.valid for m in previous) < 2:
                reasons = sorted({m.reject_reason for m in previous if m.reject_reason})
                if len(previous) < 2:
                    reasons.append('too_few_proposals')
                ctx.emit('mutation_retry', {'item_id': item.id, 'origin': origin, 'reasons': reasons})
                retries.append((item, origin, reasons))
        responses = await asyncio.gather(*(generate(item, origin, reasons)
                                           for item, origin, reasons in retries))
        require_tree(ctx, Snapshot(ctx.project_dir))
        for (item, origin, _), response in zip(retries, responses, strict=True):
            accept(item, origin, response)
        for item in eligible:
            mutations = ctx.state.mutations[item.id]
            ctx.emit('mutations_ready', {'item_id': item.id,
                     'valid': sum(m.valid for m in mutations),
                     'rejected': sum(not m.valid for m in mutations),
                     'by_origin': {origin: {'valid': sum(m.valid for m in mutations if m.origin == origin),
                                             'rejected': sum(not m.valid for m in mutations if m.origin == origin)}
                                   for origin in ('targeted', 'blind')}})
