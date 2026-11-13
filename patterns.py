"""Bugs found by reading the syntax tree, with no model involved.

The audit's recall is set by whoever raises claims, not by whoever judges them:
a judge cannot confirm a bug nobody mentioned. Tuning the judge improves
precision only. Measured on a probe carrying a dict mutated while being
iterated -- which raises on the fourth insert -- neither the 12b nor the 26b
reviewer identified it, and one described it as an efficiency concern that the
rebuttal pass then dismissed as "functionally correct". Both audits reported it
as absent.

A detector for that pattern finds it every time, in milliseconds, and cannot be
talked out of its verdict. So the patterns that are mechanically decidable are
decided mechanically, the same way a syntax error is a fact rather than an
opinion, and the models are left the questions that actually need judgement.

Only unambiguous patterns belong here. A detector that fires on correct code
costs more than it saves, because a false alarm triggers work.
"""
from __future__ import annotations

import ast
import os
from typing import Any, Dict, List

# Methods that change a container's membership. Rebinding an element is fine
# while iterating; adding or removing one is not.
_MUTATORS = {"append", "extend", "insert", "remove", "pop", "clear", "add",
             "discard", "update", "popitem", "setdefault"}


def _name_of(node: ast.AST) -> str:
    """The variable a loop walks, or "" when it is an expression we cannot
    reason about safely."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return "%s.%s" % (base, node.attr) if base else ""
    return ""


def _iterated_name(node: ast.AST) -> str:
    """What a `for` statement actually iterates, seeing through .keys() etc.

    Returns "" when the iterable is copied first. `for k in list(d)` and
    `for k in d.copy()` are the standard fixes for this very bug, so treating
    them as iteration over `d` would flag the correct code.
    """
    if isinstance(node, ast.Call):
        fn = node.func
        if isinstance(fn, ast.Name) and fn.id in ("list", "tuple", "sorted",
                                                  "set", "frozenset", "dict"):
            return ""
        if isinstance(fn, ast.Attribute):
            if fn.attr in ("copy", "items", "keys", "values"):
                # d.items() and d.keys() are live views, so mutation during
                # iteration still raises; d.copy() is safe.
                return "" if fn.attr == "copy" else _name_of(fn.value)
        return ""
    return _name_of(node)


class _Visitor(ast.NodeVisitor):
    def __init__(self, rel: str):
        self.rel = rel
        self.found: List[Dict[str, str]] = []

    def _report(self, claim, node, symbols, why, end=None):
        self.found.append({
            "claim": claim,
            "file": self.rel,
            "lines": "%d-%d" % (getattr(node, "lineno", 0),
                                end or getattr(node, "end_lineno", 0)
                                or getattr(node, "lineno", 0)),
            "symbols": symbols,
            "why": why,
        })

    def visit_For(self, node: ast.For) -> None:
        target = _iterated_name(node.iter)
        if target:
            for inner in ast.walk(node):
                hit = self._mutation_of(inner, target)
                if hit:
                    self._report(
                        "`%s` is modified while it is being iterated, which "
                        "raises RuntimeError partway through the loop." % target,
                        node, target,
                        "The loop at line %d iterates `%s` and %s inside the "
                        "body. Adding or removing elements during iteration "
                        "invalidates the iterator; CPython detects the size "
                        "change and raises `RuntimeError: dictionary changed "
                        "size during iteration` (or the set/list equivalent). "
                        "Iterate over a copy, or collect the keys first and "
                        "delete afterwards." % (node.lineno, target, hit))
                    break
        self.generic_visit(node)

    def _mutation_of(self, node: ast.AST, target: str) -> str:
        if isinstance(node, ast.Delete):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and _name_of(t.value) == target:
                    return "deletes from it at line %d" % node.lineno
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (node.func.attr in _MUTATORS
                    and _name_of(node.func.value) == target):
                return "calls `%s.%s()` at line %d" % (target, node.func.attr,
                                                       node.lineno)
        return ""

    def _check_defaults(self, node) -> None:
        for default in list(node.args.defaults) + list(
                node.args.kw_defaults or []):
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                kind = type(default).__name__.lower()
                self._report(
                    "`%s` has a mutable default argument, which is created "
                    "once and shared by every call." % node.name,
                    default, node.name,
                    "The default is evaluated a single time when the function "
                    "is defined, not per call, so mutating it leaks state "
                    "between callers. Default to None and build the %s inside "
                    "the body." % kind)

    def visit_FunctionDef(self, node) -> None:
        self._check_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node) -> None:
        self._check_defaults(node)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.Is, ast.IsNot)):
                continue
            if isinstance(comparator, ast.Constant) and not isinstance(
                    comparator.value, (bool, type(None), type(...))):
                self._report(
                    "identity comparison against the literal %r, which "
                    "depends on interpreter interning rather than value."
                    % (comparator.value,),
                    node, "",
                    "`is` asks whether two names point at the same object. "
                    "Small integers and short strings happen to be interned "
                    "and compare true by accident; the same code fails for "
                    "values outside that range. Use `==`.")
        self.generic_visit(node)


def scan(path: str, root: str = ".") -> List[Dict[str, str]]:
    """Every mechanically decidable finding in one file."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source, path)
    except (OSError, SyntaxError):
        # Unparseable files are the parse stage's business, not this one's.
        return []
    visitor = _Visitor(os.path.relpath(path, root))
    visitor.visit(tree)
    return visitor.found


def scan_all(paths: List[str], root: str = ".") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in paths:
        out.extend(scan(path, root))
    return out
