"""Cross-file reference checking for generated packages.

Per-file gates are blind to the failure that actually happens when several
models write one program at once. Each file parses, imports, runs its own
`__main__`, and passes its own test -- and then the parts do not fit, because
every component invented the interface it expected from its neighbours.

Measured on two real runs in one evening, every defect was of this kind and
none were logic errors:

  * `main.py` imported `PlayerShip`, `SpaceInvader` and `GameState` from a
    module defining four functions and no classes.
  * `enemy.py` ended with `Enemy = EnemyWave(...)`, rebinding a class name to
    an instance at import time, so importers received an object they then
    tried to call.
  * `main.py` called `player.get_rect()` and `enemy.get_invader_units()`,
    neither of which exists on the classes it had imported.
  * `shapes.py` drew through a `surface.draw_circle` API of its own invention
    while the caller passed it a pygame Surface, so it silently printed
    instead of drawing and the window stayed blank.

All four are decidable from the syntax tree, which makes them the same kind of
finding as `patterns.py`: facts, not opinions, settled before any model is
asked. A reviewer reading one file cannot see them at all, and a reviewer shown
every file has been measured to miss them anyway.

The checks are deliberately conservative. Anything requiring real type
inference is skipped rather than guessed, because a checker that fires on
correct code costs more than it saves -- the repair loop would then rewrite
working files to satisfy a phantom.
"""
from __future__ import annotations

import ast
import os
from typing import Dict, List, Optional, Set, Tuple


class Finding:
    """One mechanically established mismatch between two files."""

    def __init__(self, path: str, line: int, message: str, detail: str = ""):
        self.path = path
        self.line = line
        self.message = message
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Finding(%s:%d %s)" % (self.path, self.line, self.message)

    def text(self) -> str:
        out = "%s:%d  %s" % (self.path, self.line, self.message)
        if self.detail:
            # On the same line as the message, not a second one. Both
            # `team.why_it_failed` and `team.why` -- built for tracebacks,
            # where the last line is always the exception -- read a
            # multi-line failure by picking one line out of it, and a
            # finding split across two lines put the wrong one in an
            # operator's hands: `main.py calls client.get_ui_url(...), but
            # Client has no such attribute` (the actual defect) on one
            # line and `it defines: Client, base64, hashlib, ...` (context
            # for it) on the next. Both heuristics kept the second line and
            # silently discarded the first, so a run's summary and
            # `replan_component`'s own prompt both read "it defines: ..."
            # with no idea what was being looked for in the first place.
            # Message and detail are one logical finding; they belong on
            # one logical line.
            out += " (%s)" % self.detail
        return out


class ModuleInfo:
    """What a module offers to anything importing it."""

    def __init__(self, path: str, tree: ast.Module):
        self.path = path
        self.tree = tree
        self.classes: Dict[str, ast.ClassDef] = {}
        self.functions: Set[str] = set()
        self.assigned: Set[str] = set()
        self.rebound_classes: List[Tuple[str, int, str]] = []
        self._scan()

    @property
    def exported(self) -> Set[str]:
        return set(self.classes) | self.functions | self.assigned

    def _scan(self) -> None:
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef):
                self.classes[node.name] = node
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    self.assigned.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.assigned.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.assigned.add(node.target.id)

        # A module-level assignment that overwrites a class defined in the same
        # file with an instance of something is always a mistake: importers ask
        # for the name expecting the class and receive an object.
        defined_classes = set(self.classes)
        for node in self.tree.body:
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            called = _called_name(node.value.func)
            for target in node.targets:
                if (isinstance(target, ast.Name) and target.id in defined_classes
                        and called and called != target.id):
                    self.rebound_classes.append(
                        (target.id, node.lineno, called))

    def methods_of(self, class_name: str, index: "Index",
                   seen: Optional[Set[str]] = None) -> Optional[Set[str]]:
        """Attribute names available on a class, or None if it cannot be known.

        Returns None whenever the class inherits from something outside the
        project or does anything dynamic, because in that case absence from the
        class body proves nothing.
        """
        seen = seen or set()
        if class_name in seen:
            return set()
        seen.add(class_name)
        node = self.classes.get(class_name)
        if node is None:
            return None

        names: Set[str] = set()
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(item.name)
                if item.name == "__init__":
                    names |= _self_attributes(item)
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                names.add(item.target.id)

        for base in node.bases:
            base_name = _called_name(base)
            if not base_name:
                return None  # something dynamic; refuse to guess
            owner = index.owner_of_class(base_name)
            if owner is None:
                return None  # inherits from outside the project
            inherited = owner.methods_of(base_name, index, seen)
            if inherited is None:
                return None
            names |= inherited

        # A class that defines __getattr__ can answer to anything.
        if "__getattr__" in names or "__getattribute__" in names:
            return None
        return names


class Index:
    """Every module in the tree, keyed by the name another file would import."""

    def __init__(self, paths: List[str], root: str):
        self.root = root
        self.modules: Dict[str, ModuleInfo] = {}
        for rel in paths:
            full = os.path.join(root, rel)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    tree = ast.parse(fh.read())
            except (OSError, SyntaxError):
                continue
            self.modules[_module_key(rel)] = ModuleInfo(rel, tree)

    def resolve(self, module: str, importer: str) -> Optional[ModuleInfo]:
        """Find a module by the name used in an import statement."""
        name = module.lstrip(".")
        if not name:
            return None
        direct = self.modules.get(name)
        if direct is not None:
            return direct
        # `from package.thing import x` and `from .thing import x` should both
        # reach thing.py, so fall back to matching on the final segment.
        tail = name.split(".")[-1]
        candidates = [m for key, m in self.modules.items()
                      if key.split(".")[-1] == tail and m.path != importer]
        return candidates[0] if len(candidates) == 1 else None

    def owner_of_class(self, class_name: str) -> Optional[ModuleInfo]:
        owners = [m for m in self.modules.values() if class_name in m.classes]
        return owners[0] if len(owners) == 1 else None


def _module_key(rel: str) -> str:
    stem = rel[:-3] if rel.endswith(".py") else rel
    parts = [p for p in stem.split(os.sep) if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _called_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _self_attributes(func: ast.FunctionDef) -> Set[str]:
    """Attributes assigned to self inside __init__."""
    found: Set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                found.add(node.attr)
    return found


def _suggest(name: str, options: Set[str]) -> str:
    """Name the closest available alternative, when there is an obvious one."""
    lowered = name.lower()
    near = [o for o in options
            if o.lower() == lowered or lowered in o.lower() or o.lower() in lowered]
    if near:
        return "did you mean %s?" % ", ".join(sorted(near)[:3])
    listed = sorted(options)
    if not listed:
        return "it defines nothing at module level"
    return "it defines: %s" % ", ".join(listed[:8])


def check_imports(index: Index) -> List[Finding]:
    """Every name imported from a project module must exist in that module."""
    findings: List[Finding] = []
    for module in index.modules.values():
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                if not (isinstance(node, ast.ImportFrom) and node.level):
                    continue
            target = index.resolve(node.module or "", module.path)
            if target is None:
                continue  # third-party or unknown: not ours to judge
            available = target.exported
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name not in available:
                    findings.append(Finding(
                        module.path, node.lineno,
                        "imports %s from %s, which does not define it"
                        % (alias.name, target.path),
                        _suggest(alias.name, available)))
    return findings


def check_rebound_classes(index: Index) -> List[Finding]:
    """A class name must still be a class after the module finishes importing."""
    findings: List[Finding] = []
    for module in index.modules.values():
        for name, line, called in module.rebound_classes:
            findings.append(Finding(
                module.path, line,
                "rebinds the class %s to an instance of %s at import time"
                % (name, called),
                "anything doing `from %s import %s` receives an object, not the "
                "class, and calling it fails. Give the instance a different name."
                % (_module_key(module.path), name)))
    return findings


def _dynamic_surface(cls: ast.ClassDef) -> bool:
    """True when the set of attributes this class has cannot be known by reading.

    Anything inheriting from outside the file, defining `__getattr__`, or
    calling `setattr(self, ...)` can carry attributes that never appear as an
    assignment, so nothing may be concluded from their absence.
    """
    if cls.bases or cls.keywords:
        return True
    for node in ast.walk(cls):
        if isinstance(node, ast.FunctionDef) and node.name in (
                "__getattr__", "__getattribute__", "__setattr__"):
            return True
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("setattr", "vars", "globals", "exec")):
            return True
    return False


def _bound_on_self(cls: ast.ClassDef) -> Set[str]:
    """Every name this class ever puts on an instance, however it does it."""
    bound = {n.name for n in cls.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef))}
    for stmt in cls.body:                       # class-level attributes
        if isinstance(stmt, ast.Assign):
            bound |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            bound.add(stmt.target.id)
    for node in ast.walk(cls):
        # Stores cover `self.x =`, `self.x, self.y =`, `for self.x in`,
        # `with ... as self.x` and augmented assignment alike.
        if (isinstance(node, ast.Attribute)
                and not isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            bound.add(node.attr)
    return bound


def check_self_attributes(index: Index) -> List[Finding]:
    """An attribute read off `self` that the class never puts there.

    Decidable without running anything, and worth deciding: a repair pass
    rewriting a working line produced `self.current_or_room.exits[...]` on a
    line whose own condition tested `self.current_room.exits`, ten characters
    earlier. It parsed, imported, and passed every gate, because in isolation
    nothing about it is wrong -- the name is simply never assigned. The program
    raised AttributeError the first time a player tried to walk through a door.

    Deliberately silent on any class whose surface cannot be read off the file:
    inheritance, `__getattr__` and `setattr` all mean an attribute can exist
    without ever being assigned, and a detector that fires on correct code
    costs more than it saves.
    """
    findings: List[Finding] = []
    for module in index.modules.values():
        for cls in ast.walk(module.tree):
            if not isinstance(cls, ast.ClassDef) or _dynamic_surface(cls):
                continue
            bound = _bound_on_self(cls)
            seen: Set[str] = set()
            for node in ast.walk(cls):
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.ctx, ast.Load)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "self"
                        and node.attr not in bound
                        and node.attr not in seen):
                    seen.add(node.attr)
                    findings.append(Finding(
                        module.path, node.lineno,
                        "reads self.%s, which %s never assigns"
                        % (node.attr, cls.name),
                        _suggest(node.attr, bound)))
    return findings


def check_attributes(index: Index) -> List[Finding]:
    """Methods called on locally-constructed objects must exist on their class.

    Only variables assigned directly from a call to a class defined in this
    project are tracked, and only when that class's full surface is knowable.
    Everything else is left alone.
    """
    findings: List[Finding] = []
    for module in index.modules.values():
        for scope in _scopes(module.tree):
            bindings: Dict[str, Tuple[str, ModuleInfo]] = {}
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                    cls = _called_name(node.value.func)
                    owner = index.owner_of_class(cls) if cls else None
                    if owner is None:
                        continue
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            bindings[target.id] = (cls, owner)
                        elif (isinstance(target, ast.Attribute)
                              and isinstance(target.value, ast.Name)
                              and target.value.id == "self"):
                            bindings["self." + target.attr] = (cls, owner)

            for node in ast.walk(scope):
                # Assigning `client.token = value` is always legal Python
                # regardless of what the class declared -- an instance can
                # carry any attribute nothing pre-registered for it -- so
                # only a *read* (`ast.Load`: a call, or using the value) is
                # ever a defect. Measured live: an earlier attempt set
                # `client.api_jwt_token = api_jwt_token`, this check flagged
                # the assignment itself as "has no such attribute", and the
                # repair loop -- reading that as an instruction to make the
                # attribute exist -- subclassed the client and added a
                # method just to satisfy a finding that was never a bug.
                if (not isinstance(node, ast.Attribute)
                        or not isinstance(node.ctx, ast.Load)):
                    continue
                key = _binding_key(node.value)
                if key is None or key not in bindings:
                    continue
                cls, owner = bindings[key]
                surface = owner.methods_of(cls, index)
                if surface is None:
                    continue
                if node.attr not in surface:
                    findings.append(Finding(
                        module.path, node.lineno,
                        "calls %s.%s, but %s in %s has no such attribute"
                        % (key, node.attr, cls, owner.path),
                        _suggest(node.attr, surface)))
    return findings


def _binding_key(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == "self"):
        return "self." + node.attr
    return None


def _scopes(tree: ast.Module) -> List[ast.AST]:
    """Module body plus each class, so `self.x = Thing()` is seen once."""
    out: List[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            out.append(node)
    return out


def check_unused_dependencies(index: Index, deps: Dict[str, List[str]],
                              paths_by_component: Dict[str, str]
                              ) -> List[Finding]:
    """A component that declared a dependency must actually import from it.

    The failure this catches is the quietest one there is, because the result
    runs perfectly: rather than importing its neighbours, the joining component
    reimplements them inline as stubs. Observed live -- a `main.py` that
    declared three dependencies, imported from none of them, defined its own
    `Shape` and `GameEngine`, and gave `update()` a body of
    `# Placeholder for movement logic`. It launched a window and drew ten
    motionless squares while the real animation sat unused in another file.

    Nothing else can see this. Every gate passes, the program runs, and the
    only symptom is that the output is wrong in a way no import error reveals.
    """
    findings: List[Finding] = []
    for name, required in deps.items():
        path = paths_by_component.get(name)
        if not path or not required:
            continue
        module = index.modules.get(_module_key(path))
        if module is None:
            continue
        imported: Set[str] = set()
        for node in ast.walk(module.tree):
            if isinstance(node, ast.ImportFrom):
                target = index.resolve(node.module or "", module.path)
                if target is not None:
                    imported.add(_module_key(target.path))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])

        for dep in required:
            dep_path = paths_by_component.get(dep)
            if not dep_path:
                continue
            # A dependency on a page, a stylesheet or a data file is real and
            # is not an import. A script that reads applet_template.html at
            # runtime satisfies it perfectly, and demanding `import
            # applet_template` is an instruction no correct file can follow --
            # measured, it burned both repair rounds rewriting a working
            # main.py and then reported the plan as unresolved.
            if not dep_path.endswith(".py"):
                continue
            key = _module_key(dep_path)
            if key in imported or key.split(".")[-1] in imported:
                continue
            target = index.modules.get(key)
            offers = sorted(target.exported)[:6] if target else []
            findings.append(Finding(
                path, 1,
                "depends on %s but imports nothing from it" % dep_path,
                "use what that file defines (%s) instead of writing your own "
                "copy" % (", ".join(offers) or "see the file")))
    return findings


def scan(paths: List[str], root: str,
         deps: Optional[Dict[str, List[str]]] = None,
         paths_by_component: Optional[Dict[str, str]] = None
         ) -> List[Finding]:
    """Every cross-file check, cheapest and least arguable first."""
    index = Index(paths, root)
    findings = check_rebound_classes(index)
    findings += check_imports(index)
    findings += check_attributes(index)
    findings += check_self_attributes(index)
    if deps and paths_by_component:
        findings += check_unused_dependencies(index, deps, paths_by_component)
    findings.sort(key=lambda f: (f.path, f.line))
    return findings


def group_by_file(findings: List[Finding]) -> Dict[str, List[Finding]]:
    grouped: Dict[str, List[Finding]] = {}
    for f in findings:
        grouped.setdefault(f.path, []).append(f)
    return grouped
