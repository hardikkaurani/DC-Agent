"""Static AST Security Validator for untrusted code execution."""

import ast
from dataclasses import dataclass


@dataclass
class SecurityViolation:
    """Represents a specific detected security rule violation."""

    rule_name: str
    message: str
    line_number: int | None = None


class SecurityViolationError(Exception):
    """Raised when code fails static security validation."""

    def __init__(self, violations: list[SecurityViolation]) -> None:
        self.violations = violations
        messages = "; ".join(
            f"[L{v.line_number or '?'}] {v.rule_name}: {v.message}" for v in violations
        )
        super().__init__(f"Security validation failed: {messages}")


# Strictly disallowed module imports
FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "socket",
        "http",
        "urllib",
        "requests",
        "httpx",
        "asyncio",
        "pathlib",
        "ctypes",
        "threading",
        "multiprocessing",
        "pickle",
        "pty",
        "posix",
        "nt",
        "importlib",
        "builtins",
        "platform",
    }
)

# Forbidden built-in function calls
FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "locals",
        "breakpoint",
        "open",
    }
)

# Forbidden attribute lookups (e.g., sandbox escape vectors)
FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__subclasses__",
        "__globals__",
        "__code__",
        "__builtins__",
        "__closure__",
        "__func__",
    }
)


class SecurityASTVisitor(ast.NodeVisitor):
    """AST visitor that checks for malicious patterns and security violations."""

    def __init__(self) -> None:
        self.violations: list[SecurityViolation] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root_module = alias.name.split(".")[0]
            if root_module in FORBIDDEN_MODULES:
                self.violations.append(
                    SecurityViolation(
                        rule_name="FORBIDDEN_IMPORT",
                        message=f"Import of forbidden module '{alias.name}' is prohibited.",
                        line_number=node.lineno,
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root_module = node.module.split(".")[0]
            if root_module in FORBIDDEN_MODULES:
                self.violations.append(
                    SecurityViolation(
                        rule_name="FORBIDDEN_IMPORT_FROM",
                        message=f"Import from forbidden module '{node.module}' is prohibited.",
                        line_number=node.lineno,
                    )
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            self.violations.append(
                SecurityViolation(
                    rule_name="FORBIDDEN_CALL",
                    message=(f"Invocation of dangerous function '{node.func.id}()' is prohibited."),
                    line_number=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_ATTRIBUTES:
            self.violations.append(
                SecurityViolation(
                    rule_name="FORBIDDEN_ATTRIBUTE",
                    message=f"Access to sensitive internal attribute '{node.attr}' is prohibited.",
                    line_number=node.lineno,
                )
            )
        self.generic_visit(node)


def validate_code_safety(source_code: str) -> None:
    """Parse and statically validate Python source code for sandbox execution.

    Raises:
        SecurityViolationError: If dangerous imports, calls, or attributes are detected.
        SyntaxError: If the source code cannot be parsed as valid Python.
    """
    tree = ast.parse(source_code)
    visitor = SecurityASTVisitor()
    visitor.visit(tree)

    if visitor.violations:
        raise SecurityViolationError(visitor.violations)
