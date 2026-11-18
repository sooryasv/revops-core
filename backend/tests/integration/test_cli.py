from __future__ import annotations

import argparse
import uuid

import pytest
from sqlalchemy import text

from app.cli import create_org
from app.config import Settings
from app.db.session import make_engine

pytestmark = pytest.mark.integration


def _args(**kw: object) -> argparse.Namespace:
    base = {
        "name": "CLI Co", "legal_name": None, "state_code": "33", "gstin": None,
        "admin_email": f"cli.{uuid.uuid4().hex[:8]}@example.com", "admin_name": "CLI Admin",
    }  # fmt: skip
    base.update(kw)
    return argparse.Namespace(**base)


async def test_create_org_creates_org_admin_and_audit_row(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "a-long-enough-password")
    args = _args()
    assert await create_org(args, settings) == 0
    assert "created organization" in capsys.readouterr().out
    engine = make_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT u.role, u.password_hash, o.name FROM users u JOIN organizations o ON o.id=u.org_id WHERE u.email=:e"
                    ),
                    {"e": args.admin_email},
                )
            ).one()
            audits = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM audit_logs WHERE action='org.create' AND after->>'admin_email'=:e"
                    ),
                    {"e": args.admin_email},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert row[0] == "admin"
    assert row[1].startswith("$argon2id$")
    assert "a-long-enough-password" not in row[1]
    assert audits == 1


async def test_create_org_rejects_bad_input_without_writing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADMIN_PASSWORD", "a-long-enough-password")
    args = _args(state_code="00")
    assert await create_org(args, settings) == 2
    assert "error:" in capsys.readouterr().err
    monkeypatch.setenv("ADMIN_PASSWORD", "short")
    assert await create_org(_args(), settings) == 2
