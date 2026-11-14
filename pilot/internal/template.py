import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})", re.S)

# Remove the newline after a block or comment tag when it is alone on a line.
BLOCK_LINE_RE = re.compile(
    r"^[ \t]*((?:{%.*?%}|{#.*?#}))[ \t]*(?:\r?\n|$)",
    re.M,
)

SAFE_GLOBALS = {
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "min": min,
    "max": max,
    "sum": sum,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
}


class Node:
    pass


@dataclass
class Text(Node):
    value: str


@dataclass
class Expr(Node):
    value: str


@dataclass
class If(Node):
    condition: str
    body: list[Node]
    elifs: list[tuple[str, list[Node]]]
    else_body: list[Node]


@dataclass
class For(Node):
    variable: str
    iterable: str
    body: list[Node]


class Parser:
    def __init__(self, template: str):
        template = BLOCK_LINE_RE.sub(r"\1", template)

        self.tokens = [part for part in TOKEN_RE.split(template) if part]
        self.pos = 0

    def current(self) -> str | None:
        """The token at the current position, or None at the end."""
        if self.pos >= len(self.tokens):
            return None

        return self.tokens[self.pos]

    def consume(self) -> str:
        """Return the current token and move past it; raise at the end."""
        token = self.current()

        if token is None:
            raise ValueError("Unexpected end of template")

        self.pos += 1
        return token

    @staticmethod
    def statement(token: str) -> str:
        """The inside of a `{% %}` tag."""
        return token[2:-2].strip()

    @staticmethod
    def keyword(statement: str) -> str:
        """The first word of a statement."""
        if not statement:
            return ""

        return statement.split(maxsplit=1)[0]

    def parse(self) -> list[Node]:
        """Parse every token into nodes; raise on a stray closing tag."""
        nodes = self.parse_block(set())

        if self.current() is not None:
            raise ValueError(f"Unexpected token: {self.current()}")

        return nodes

    def parse_block(self, stop: set[str]) -> list[Node]:
        """Parse nodes until a statement whose keyword is in stop."""
        nodes: list[Node] = []

        while (token := self.current()) is not None:
            if not TOKEN_RE.fullmatch(token):
                nodes.append(self.parse_text())
                continue

            if token.startswith("{{"):
                nodes.append(self.parse_expr())
                continue

            if token.startswith("{#"):
                self.consume()
                continue

            statement = self.statement(token)
            keyword = self.keyword(statement)

            if keyword in stop:
                break

            nodes.append(self.parse_statement(keyword, statement))

        return nodes

    def parse_text(self) -> Text:
        """A text node; raise when it holds an unclosed tag."""
        token = self.consume()
        if any(opener in token for opener in ("{{", "{%", "{#")):
            raise ValueError(f"Unterminated tag: {token[:40]!r}")

        return Text(token)

    def parse_expr(self) -> Expr:
        """An expression node from a `{{ }}` tag."""
        token = self.consume()
        expression = token[2:-2].strip()

        if not expression:
            raise ValueError("Empty expression")

        return Expr(expression)

    def parse_statement(
        self,
        keyword: str,
        statement: str,
    ) -> Node:
        """The node for an if or for statement; raise on any other keyword."""
        handlers = {
            "if": self.parse_if,
            "for": self.parse_for,
        }

        handler = handlers.get(keyword)

        if handler is None:
            raise ValueError(f"Unknown statement: {statement}")

        return handler(statement)

    def parse_if(self, statement: str) -> If:
        """An if node with its elif and else branches."""
        condition = statement[2:].strip()

        if not condition:
            raise ValueError("Missing if condition")

        self.consume()

        body = self.parse_block({"elif", "else", "endif"})
        elifs: list[tuple[str, list[Node]]] = []
        else_body: list[Node] = []

        while (token := self.current()) is not None:
            current_statement = self.statement(token)
            keyword = self.keyword(current_statement)

            if keyword == "elif":
                elifs.append(self.parse_elif(current_statement))
                continue

            if keyword == "else":
                else_body = self.parse_else()
                continue

            if keyword == "endif":
                self.consume()
                return If(
                    condition,
                    body,
                    elifs,
                    else_body,
                )

            raise ValueError(f"Expected endif, got: {current_statement}")

        raise ValueError("Missing {% endif %}")

    def parse_elif(
        self,
        statement: str,
    ) -> tuple[str, list[Node]]:
        """The condition and body of one elif branch."""
        condition = statement[4:].strip()

        if not condition:
            raise ValueError("Missing elif condition")

        self.consume()

        body = self.parse_block({"elif", "else", "endif"})

        return condition, body

    def parse_else(self) -> list[Node]:
        """The body of an else branch."""
        statement = self.statement(self.consume())

        if statement != "else":
            raise ValueError(f"Invalid else statement: {statement}")

        return self.parse_block({"endif"})

    def parse_for(self, statement: str) -> For:
        """A for node from `for name in iterable`."""
        match = re.fullmatch(
            r"for\s+(\w+)\s+in\s+(.+)",
            statement,
            re.S,
        )

        if match is None:
            raise ValueError(f"Invalid for statement: {statement}")

        self.consume()

        body = self.parse_block({"endfor"})
        self.consume_expected("endfor")

        return For(
            variable=match.group(1),
            iterable=match.group(2).strip(),
            body=body,
        )

    def consume_expected(self, expected: str) -> None:
        """Consume the expected closing tag; raise when it is missing."""
        token = self.current()

        if token is None:
            raise ValueError(f"Missing {{% {expected} %}}")

        statement = self.statement(token)

        if statement != expected:
            raise ValueError(f"Expected {expected}, got: {statement}")

        self.consume()


class Template:
    def __init__(self, template: str):
        self.ast = Parser(template).parse()

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        encoding: str = "utf-8",
    ) -> "Template":
        """A template read from a file."""
        content = Path(path).read_text(encoding=encoding)
        return cls(content)

    @staticmethod
    def eval_expr(
        expression: str,
        context: dict[str, Any],
    ) -> Any:
        """Evaluate an expression with no builtins except the safe globals."""
        return eval(
            expression,
            {"__builtins__": {}},
            SAFE_GLOBALS | context,
        )

    def render(self, **context: Any) -> str:
        """The template rendered with these values."""
        return self.render_nodes(
            self.ast,
            context,
        )

    def render_nodes(
        self,
        nodes: list[Node],
        context: dict[str, Any],
    ) -> str:
        """The nodes rendered and joined."""
        output = [self.render_node(node, context) for node in nodes]

        return "".join(output)

    def render_node(
        self,
        node: Node,
        context: dict[str, Any],
    ) -> str:
        """One node rendered."""
        if isinstance(node, Text):
            return node.value

        if isinstance(node, Expr):
            value = self.eval_expr(
                node.value,
                context,
            )
            return "" if value is None else str(value)

        if isinstance(node, If):
            return self.render_if(node, context)

        if isinstance(node, For):
            return self.render_for(node, context)

        raise TypeError(f"Unsupported node: {type(node).__name__}")

    def render_if(
        self,
        node: If,
        context: dict[str, Any],
    ) -> str:
        """The first branch whose condition holds, rendered."""
        if self.eval_expr(node.condition, context):
            return self.render_nodes(
                node.body,
                context,
            )

        for condition, body in node.elifs:
            if self.eval_expr(condition, context):
                return self.render_nodes(
                    body,
                    context,
                )

        return self.render_nodes(
            node.else_body,
            context,
        )

    def render_for(
        self,
        node: For,
        context: dict[str, Any],
    ) -> str:
        """The loop body rendered once per item."""
        iterable = self.eval_expr(
            node.iterable,
            context,
        )

        output = []

        for item in iterable:
            child_context = context | {node.variable: item}

            output.append(
                self.render_nodes(
                    node.body,
                    child_context,
                )
            )

        return "".join(output)
