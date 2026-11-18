"""Architecture rules (03 section 3 layering, section 6.4 rule 5): enforced by reading the import graph and call sites."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

# Layer -> prefixes it must NOT import. api calls services; services call repositories and domain; repositories talk to db.
FORBIDDEN: dict[str, tuple[str, ...]] = {
    "domain": ("app.api", "app.services", "app.repositories", "app.db", "app.core", "app.payments", "sqlalchemy", "fastapi", "starlette"),
    "core": ("app.api", "app.services", "app.repositories", "app.db.models"),
    "db": ("app.api", "app.services", "app.repositories"),
    "repositories": ("app.api", "app.services"),
    "services": ("app.api", "sqlalchemy"),
    "api": ("sqlalchemy", "app.repositories", "app.db.models"),
}  # fmt: skip

# Deliberate exceptions, each with a reason. Adding one is a conscious, reviewed change.
ALLOWED_IMPORTS: dict[str, dict[str, str]] = {
    "api/v1/health.py": {
        "sqlalchemy": "readiness probe runs SELECT 1 and reads the migration revision"
    },
}
# core may use these db helpers (not models); domain stays free of everything.
CORE_ALLOWED_PREFIXES = ("app.db.base", "app.db.session", "app.db.uow")

# Files permitted to call system_scope(...) and why (03 section 6.4 rule 5).
SYSTEM_SCOPE_CALL_SITES = {
    "repositories/users.py": "login lookup by globally unique email",
    "repositories/organizations.py": "tenant creation (CLI onboarding)",
    "repositories/webhooks.py": "webhook intake, outcome, failure record and order resolution",
    "repositories/public_pay.py": "public pay-token resolution",
}


def _py_files(layer: str) -> list[Path]:
    return sorted((APP / layer).rglob("*.py"))


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def _violations(layer: str) -> list[str]:
    bad: list[str] = []
    for path in _py_files(layer):
        rel = path.relative_to(APP).as_posix()
        allowed = ALLOWED_IMPORTS.get(rel, {})
        for imported in _imports(path):
            for prefix in FORBIDDEN[layer]:
                if imported == prefix or imported.startswith(prefix + "."):
                    if prefix in allowed:
                        continue
                    if layer == "core" and imported.startswith(CORE_ALLOWED_PREFIXES):
                        continue
                    bad.append(f"{rel} imports {imported} (layer '{layer}' may not use {prefix})")
    return bad


@pytest.mark.parametrize("layer", list(FORBIDDEN))
def test_layer_does_not_import_forbidden_modules(layer: str) -> None:
    assert not _violations(layer)


def test_exceptions_list_has_no_stale_entries() -> None:
    for rel in ALLOWED_IMPORTS:
        assert (APP / rel).exists(), f"{rel} no longer exists; remove it from ALLOWED_IMPORTS"


def test_services_never_build_queries() -> None:
    """Tenant queries live in repositories/. Services may flush or refresh, never select/execute."""
    offenders: list[str] = []
    for path in _py_files("services"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"execute", "scalars", "scalar"}:
                offenders.append(f"{path.relative_to(APP)}:{node.lineno} .{node.attr}")
            if isinstance(node, ast.Name) and node.id in {
                "select",
                "text",
                "update",
                "delete",
                "insert",
            }:
                offenders.append(f"{path.relative_to(APP)}:{node.lineno} {node.id}")
    assert not offenders


def test_system_scope_call_sites_are_the_approved_ones() -> None:
    calls: set[str] = set()
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else ""
                )
                if name == "system_scope":
                    calls.add(path.relative_to(APP).as_posix())
    assert calls == set(SYSTEM_SCOPE_CALL_SITES), (
        f"system_scope may only be called from {sorted(SYSTEM_SCOPE_CALL_SITES)}; found {sorted(calls)}"
    )


def test_no_string_built_sql_in_application_code() -> None:
    """SQL is ORM or bound-parameter `text()` only: no f-strings or concatenation passed to text()/execute()."""
    offenders: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and node.args:
                func = node.func
                name = (
                    func.id
                    if isinstance(func, ast.Name)
                    else func.attr
                    if isinstance(func, ast.Attribute)
                    else ""
                )
                if name in {"text", "execute"} and isinstance(
                    node.args[0], ast.JoinedStr | ast.BinOp
                ):
                    offenders.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert not offenders


def test_no_forbidden_libraries_are_used() -> None:
    """00 B5: passlib and python-jose are banned (unmaintained / security history)."""
    banned = ("passlib", "jose", "python_jose")
    offenders = [
        f"{p.relative_to(APP)} imports {i}"
        for p in sorted(APP.rglob("*.py"))
        for i in _imports(p)
        if i.split(".")[0] in banned
    ]
    assert not offenders
