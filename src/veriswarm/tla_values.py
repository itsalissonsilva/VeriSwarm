"""Parser for the TLA+ value syntax TLC prints in traces and state-graph dumps.

Handles the fragment TLC emits for this project: integers, strings, booleans,
sequences, sets, records and `@@`-style functions. Anything else raises rather
than being guessed at, so a format change fails loudly.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(
    r"""\s*(?:
      (?P<int>-?\d+)
    | (?P<str>"(?:[^"\\]|\\.)*")
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<sym><<|>>|\|->|:>|@@|[\[\]\{\},\(\)])
    )""",
    re.VERBOSE,
)


class TLAParseError(ValueError):
    pass


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        m = _TOKEN.match(text, i)
        if not m or m.end() == i:
            raise TLAParseError(f"cannot tokenize at {i}: {text[i:i+40]!r}")
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        i = m.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.t = tokens
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise TLAParseError("unexpected end of input")
        self.i += 1
        return tok

    def expect(self, sym: str) -> None:
        tok = self.next()
        if tok[1] != sym:
            raise TLAParseError(f"expected {sym!r}, found {tok[1]!r}")

    def value(self):
        kind, text = self.next()
        if kind == "int":
            return int(text)
        if kind == "str":
            return bytes(text[1:-1], "utf-8").decode("unicode_escape")
        if kind == "name":
            if text == "TRUE":
                return True
            if text == "FALSE":
                return False
            raise TLAParseError(f"unexpected name {text!r}")
        if text == "<<":
            return self._list(">>")
        if text == "{":
            return sorted(self._list("}"), key=repr)
        if text == "[":
            return self._record()
        if text == "(":
            return self._function()
        raise TLAParseError(f"unexpected token {text!r}")

    def _list(self, close: str) -> list:
        items = []
        if self.peek() and self.peek()[1] == close:
            self.next()
            return items
        while True:
            items.append(self.value())
            tok = self.next()
            if tok[1] == close:
                return items
            if tok[1] != ",":
                raise TLAParseError(f"expected ',' or {close!r}, found {tok[1]!r}")

    def _record(self) -> dict:
        out = {}
        while True:
            kind, key = self.next()
            if kind != "name":
                raise TLAParseError(f"expected a field name, found {key!r}")
            self.expect("|->")
            out[key] = self.value()
            tok = self.next()
            if tok[1] == "]":
                return out
            if tok[1] != ",":
                raise TLAParseError(f"expected ',' or ']', found {tok[1]!r}")

    def _function(self) -> dict:
        out = {}
        while True:
            key = self.value()
            self.expect(":>")
            out[key] = self.value()
            tok = self.next()
            if tok[1] == ")":
                return out
            if tok[1] != "@@":
                raise TLAParseError(f"expected '@@' or ')', found {tok[1]!r}")


def parse_value(text: str):
    parser = _Parser(_tokenize(text))
    value = parser.value()
    if parser.peek() is not None:
        raise TLAParseError(f"trailing input: {parser.peek()!r}")
    return value


_VAR_SPLIT = re.compile(r"^/\\ ([A-Za-z_][A-Za-z0-9_]*) = ", re.MULTILINE)


def parse_state(text: str) -> dict:
    """Parses `/\\ x = value` conjunctions into a {variable: value} dictionary."""
    matches = list(_VAR_SPLIT.finditer(text))
    if not matches:
        raise TLAParseError(f"no variable assignments in {text[:80]!r}")
    out = {}
    for m, nxt in zip(matches, [*matches[1:], None]):
        end = nxt.start() if nxt else len(text)
        out[m.group(1)] = parse_value(text[m.end():end])
    return out
