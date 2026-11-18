"""Architecture guards for the pure domain package (03 section 3, 00 B9, 04 M1)."""

import ast
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[2] / "app" / "domain"
FORBIDDEN_IMPORT_ROOTS = {
    "sqlalchemy",
    "fastapi",
    "starlette",
    "pydantic",
    "httpx",
    "asyncpg",
    "structlog",
}


def _trees() -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text())) for p in DOMAIN.glob("*.py")]


def test_no_float_or_decimal_anywhere_in_domain() -> None:
    offenders: list[str] = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append(f"{path.name}:{node.lineno} float literal")
            if isinstance(node, ast.Name) and node.id in {"float", "Decimal"}:
                offenders.append(f"{path.name}:{node.lineno} {node.id}")
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                offenders.append(f"{path.name}:{node.lineno} true division")
    assert offenders == []


def test_domain_imports_no_framework_or_other_app_layers() -> None:
    offenders: list[str] = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for m in modules:
                root = m.split(".")[0]
                if root in FORBIDDEN_IMPORT_ROOTS or (
                    root == "app" and not m.startswith("app.domain")
                ):
                    offenders.append(f"{path.name}: {m}")
    assert offenders == []
