"""Source contracts for the small, dependency-free browser application.

These are wiring checks, not browser behavior tests. A small lexer keeps comments,
regular expressions and HTML out of JavaScript declarations. HTMLParser reads only
actual tags in string/template literals, so selector strings do not count as UI.
Template expressions are inspected as code and represented by a marker in HTML.
Existing test_quantify/test_supply checks own classes and plain-language copy.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DYNAMIC = "__EXPRESSION__"


@dataclass
class Token:
    kind: str
    value: str
    start: int
    end: int


def lex(source: str) -> list[Token]:
    """Tokenize the syntax needed by these contracts, including nested templates."""
    tokens: list[Token] = []
    size = len(source)

    def scan(at: int, interpolation: bool = False) -> int:
        depth = 0
        previous = ""
        while at < size:
            char = source[at]
            if char.isspace():
                at += 1
                continue
            if source.startswith("//", at):
                end = source.find("\n", at)
                at = size if end < 0 else end + 1
                continue
            if source.startswith("/*", at):
                end = source.find("*/", at + 2)
                at = size if end < 0 else end + 2
                continue
            start = at
            if char in "\"'":
                at += 1
                while at < size:
                    if source[at] == "\\":
                        at += 2
                    elif source[at] == char:
                        at += 1
                        break
                    else:
                        at += 1
                tokens.append(Token("string", source[start + 1:at - 1], start, at))
                previous = "value"
                continue
            if char == "`":
                token = Token("template", "", start, start)
                tokens.append(token)
                chunks = []
                at += 1
                segment = at
                while at < size:
                    if source[at] == "\\":
                        at += 2
                    elif source[at] == "`":
                        chunks.append(source[segment:at])
                        at += 1
                        break
                    elif source.startswith("${", at):
                        chunks.append(source[segment:at] + DYNAMIC)
                        tokens.append(Token("code", "{", at + 1, at + 2))
                        at = scan(at + 2, True)
                        segment = at
                    else:
                        at += 1
                token.value, token.end = "".join(chunks), at
                previous = "value"
                continue
            # Slash after an expression-start token begins a regex, not division.
            if char == "/" and previous in ("", "=", "(", ":", ",", "[", "!", "?", "return", "=>", "&&", "||"):
                at += 1
                in_class = False
                while at < size:
                    if source[at] == "\\":
                        at += 2
                        continue
                    if source[at] == "[":
                        in_class = True
                    elif source[at] == "]":
                        in_class = False
                    elif source[at] == "/" and not in_class:
                        at += 1
                        while at < size and source[at].isalpha():
                            at += 1
                        break
                    at += 1
                tokens.append(Token("regex", source[start:at], start, at))
                previous = "value"
                continue
            word = re.match(r"[A-Za-z_$][\w$]*|\d+(?:\.\d+)?|===|!==|=>|\?\.|&&|\|\||==|!=", source[at:])
            value = word.group() if word else char
            at += len(value)
            tokens.append(Token("code", value, start, at))
            if value == "{":
                depth += 1
            elif value == "}":
                if interpolation and depth == 0:
                    return at
                depth -= 1
            previous = value
        return at

    scan(0)
    return tokens


def balanced(tokens: list[Token], start: int) -> tuple[int, int]:
    opening = tokens[start].value
    closing = {"{": "}", "[": "]", "(": ")"}[opening]
    depth = 0
    for end in range(start, len(tokens)):
        if tokens[end].kind != "code":
            continue
        if tokens[end].value == opening:
            depth += 1
        elif tokens[end].value == closing:
            depth -= 1
            if not depth:
                return start, end
    raise AssertionError(f"Unclosed {opening} at {tokens[start].start}")


def declaration(tokens: list[Token], name: str, opening: str) -> tuple[int, int]:
    for index, token in enumerate(tokens):
        if token.kind == "code" and token.value == name:
            # Callers use unique, named application helpers or constants.
            if index and tokens[index - 1].value in ("function", "const", "let"):
                start = next(i for i in range(index + 1, len(tokens)) if tokens[i].kind == "code" and tokens[i].value == opening)
                return balanced(tokens, start)
    raise AssertionError(f"Missing declaration: {name}")


def duplicates(tokens: list[Token]) -> dict[str, list[int]]:
    scopes: list[int] = []
    names: dict[tuple[tuple[int, ...], str], list[int]] = defaultdict(list)
    for index, token in enumerate(tokens):
        if token.kind != "code":
            continue
        if token.value == "function" and index + 2 < len(tokens) and tokens[index + 2].value == "(":
            name = tokens[index + 1].value
            names[tuple(scopes), name].append(token.start)
        if token.value == "{":
            scopes.append(token.start)
        elif token.value == "}" and scopes:
            scopes.pop()
    return {name: positions for (_, name), positions in names.items() if len(positions) > 1}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def markup(tokens: list[Token]) -> list[tuple[str, dict[str, str | None]]]:
    found = []
    for token in tokens:
        if token.kind in ("string", "template") and "<" in token.value:
            parser = Tags()
            parser.feed(token.value)
            found.extend(parser.tags)
    return found


class InterfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "web/app.js").read_text(encoding="utf-8")
        cls.tokens = lex(cls.source)
        cls.tags = markup(cls.tokens)

    def test_lexer_distinguishes_code_from_copy_and_nested_templates(self):
        sample = '''// function fake() {}
        const a = "function fake() {}";
        const b = /function fake\\(\\) {}/;
        function first() { function helper() {} }
        function second() { function helper() {} }
        const c = `<p>${`<button data-do="real">${(() => { function nested() {} })()}</button>`}</p>`;
        // <button data-do="fake">
        const selector = '[data-do="not-emitted"]';
        '''
        tokens = lex(sample)
        self.assertEqual(duplicates(tokens), {})
        self.assertEqual({attrs.get("data-do") for _, attrs in markup(tokens) if "data-do" in attrs}, {"real"})
        self.assertEqual(set(duplicates(lex("function same() {} function same() {}"))), {"same"})

    def test_no_duplicate_named_functions_in_the_same_scope(self):
        for path in sorted((ROOT / "web").glob("*.js")):
            self.assertEqual(duplicates(lex(path.read_text(encoding="utf-8"))), {}, path.name)

    def test_emitted_actions_have_click_cases_and_cases_have_controls(self):
        actions = {attrs["data-do"] for _, attrs in self.tags if "data-do" in attrs}
        dynamic = {value for value in actions if DYNAMIC in (value or "")}
        self.assertEqual(dynamic, set(), "Document each computed action's possible values here before adding it")
        handlers = {self.tokens[i + 1].value for i, token in enumerate(self.tokens[:-1])
                    if token.kind == "code" and token.value == "case" and self.tokens[i + 1].kind == "string"}
        # app.js has one switch, dedicated to data-do. data-supply, data-view,
        # data-update-action and data-stab deliberately use other dispatchers.
        self.assertEqual(sum(t.kind == "code" and t.value == "switch" for t in self.tokens), 1)
        self.assertEqual(actions - handlers, set(), "Emitted controls without a data-do handler")
        self.assertEqual(handlers - actions, set(), "Click cases without an emitted control")

    def test_every_emitted_form_has_an_explicit_submit_route(self):
        forms = {attrs.get("id") for tag, attrs in self.tags if tag == "form"}
        self.assertNotIn(None, forms, "A submitted form needs a stable ID")
        submit_tokens = []
        for i in range(len(self.tokens) - 3):
            if [t.value for t in self.tokens[i:i + 3]] == ["addEventListener", "(", "submit"]:
                body = next(j for j in range(i + 3, len(self.tokens))
                            if self.tokens[j].kind == "code" and self.tokens[j].value == "{")
                start, end = balanced(self.tokens, body)
                submit_tokens.extend(self.tokens[start:end])
        # The small supply submit listener forwards to this named dispatcher.
        self.assertTrue(any(t.value == "supplySubmit" for t in submit_tokens))
        start, end = declaration(self.tokens, "supplySubmit", "{")
        submit_tokens.extend(self.tokens[start:end])
        routes = set()
        for i in range(len(submit_tokens) - 5):
            values = [t.value for t in submit_tokens[i:i + 6]]
            if values[:4] == ["form", ".", "id", "==="] and submit_tokens[i + 4].kind == "string":
                routes.add(values[4])
            if values[:2] == ["formId", "==="] and submit_tokens[i + 2].kind == "string":
                routes.add(values[2])
        # f-onb appears in mutually exclusive steps, not twice in one document.
        # Supply uses formId inside supplySubmit, other submitters use form.id.
        self.assertEqual(forms - routes, set(), "Forms have no explicit submit route")
        self.assertEqual(routes - forms, set(), "Submit routes have no emitted form")

    def test_navigation_and_settings_match_the_current_product_contract(self):
        def array(name):
            start, end = declaration(self.tokens, name, "[")
            return ast.literal_eval(self.source[self.tokens[start].start:self.tokens[end].end])
        self.assertEqual(array("NAV"), [
            ["today", "Today", "today"], ["ordering", "Order", "order"],
            ["history", "History", "history"], ["updates", "Updates", "updates"],
            ["settings", "Settings", "settings"],
        ])
        self.assertEqual(array("SETTINGS_TABS"), [
            ["location", "Location"], ["menu", "Menu"], ["costs", "Costs"], ["account", "Account"],
        ])
        start, end = declaration(self.tokens, "screenFrame", "{")
        code = [t.value for t in self.tokens[start:end] if t.kind == "code"]
        self.assertIn(["NAV", ".", "map", "("], [code[i:i + 4] for i in range(len(code) - 3)],
                      "The public demo must derive its destinations from NAV")

    def test_browser_storage_and_clipboard_have_one_safe_boundary(self):
        store_start, store_end = declaration(self.tokens, "store", "{")
        copy_start, copy_end = declaration(self.tokens, "copyText", "{")
        allowed = {
            "localStorage": (self.tokens[store_start].start, self.tokens[store_end].end),
            "clipboard": (self.tokens[copy_start].start, self.tokens[copy_end].end),
        }
        for path in sorted((ROOT / "web").glob("*.js")):
            tokens = lex(path.read_text(encoding="utf-8"))
            for index, token in enumerate(tokens):
                if token.kind != "code":
                    continue
                name = token.value
                if name == "localStorage" or (name == "clipboard" and index > 1 and tokens[index - 2].value == "navigator"):
                    start, end = allowed[name]
                    self.assertTrue(path.name == "app.js" and start <= token.start < end,
                                    f"{path.name}:{token.start}: {name} bypasses its safe helper")


if __name__ == "__main__":
    unittest.main()
