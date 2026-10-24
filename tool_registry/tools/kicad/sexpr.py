"""KiCad S-expression reading and writing (FORGE-680, FORGE-683).

KiCad's schematics, boards, netlists and symbol/footprint libraries are all
S-expressions. This module reads them into nested Python lists and writes
lists back out, so the adapter works on the structure rather than on string
fragments (the pin-map parser used to split text on ``"(comp "``).

A list is ``[head, *items]``; an atom is a ``str``. Quoted strings are kept
apart from bare atoms with :class:`QStr`, so writing a file back preserves
which values KiCad quoted.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

__all__ = ["QStr", "SExprError", "children", "dumps", "find", "find_all", "loads", "value"]


class SExprError(ValueError):
    """The text is not a well-formed S-expression."""


class QStr(str):
    """A string that was (or must be) quoted in the file."""

    __slots__ = ()


def _tokens(text: str) -> Iterator[tuple[str, str]]:
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c, c
            i += 1
        elif c == '"':
            j = i + 1
            out: list[str] = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    nxt = text[j + 1]
                    out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
                    j += 2
                else:
                    out.append(text[j])
                    j += 1
            if j >= n:
                raise SExprError("unterminated string")
            yield "q", "".join(out)
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield "a", text[i:j]
            i = j


def loads(text: str) -> list[Any]:
    """Parse one top-level S-expression."""
    stack: list[list[Any]] = []
    result: list[Any] | None = None
    for kind, tok in _tokens(text):
        if result is not None:
            raise SExprError("text after the top-level expression")
        if kind == "(":
            new: list[Any] = []
            if stack:
                stack[-1].append(new)
            stack.append(new)
        elif kind == ")":
            if not stack:
                raise SExprError("unbalanced ')'")
            done = stack.pop()
            if not stack:
                result = done
        else:
            if not stack:
                raise SExprError("atom outside a list")
            stack[-1].append(QStr(tok) if kind == "q" else tok)
    if stack or result is None:
        raise SExprError("unbalanced '('")
    return result


def _atom(x: Any) -> str:
    s = str(x)
    if isinstance(x, QStr) or s == "" or any(c in s for c in ' \t\r\n()"\\'):
        esc = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{esc}"'
    return s


def dumps(expr: Any, indent: int = 0) -> str:
    """Write an expression in KiCad's style: one nested list per line."""
    if not isinstance(expr, list):
        return _atom(expr)
    pad = "  " * indent
    simple = all(not isinstance(x, list) for x in expr)
    if simple:
        return pad + "(" + " ".join(_atom(x) for x in expr) + ")"
    parts = []
    head: list[str] = []
    for x in expr:
        if isinstance(x, list):
            break
        head.append(_atom(x))
    lines = [pad + "(" + " ".join(head)]
    for x in expr[len(head) :]:
        if isinstance(x, list):
            lines.append(dumps(x, indent + 1))
        else:
            lines.append("  " * (indent + 1) + _atom(x))
    parts.append("\n".join(lines) + "\n" + pad + ")")
    return "".join(parts)


def children(expr: list[Any], head: str) -> list[list[Any]]:
    """Direct child lists of ``expr`` whose head is ``head``."""
    return [x for x in expr[1:] if isinstance(x, list) and x and x[0] == head]


def find(expr: list[Any], head: str) -> list[Any] | None:
    """The first direct child list with ``head``."""
    found = children(expr, head)
    return found[0] if found else None


def find_all(expr: list[Any], head: str) -> list[list[Any]]:
    """Every list with ``head`` anywhere below ``expr``, depth first."""
    out: list[list[Any]] = []
    for x in expr[1:]:
        if isinstance(x, list):
            if x and x[0] == head:
                out.append(x)
            out.extend(find_all(x, head))
    return out


def value(expr: list[Any], head: str, default: str = "") -> str:
    """The first atom of the child ``(head atom)``, e.g. ``value(comp, "ref")``."""
    child = find(expr, head)
    if child is None or len(child) < 2 or isinstance(child[1], list):
        return default
    return str(child[1])
