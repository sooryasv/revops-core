"""Operator commands: `python -m app.cli <command>`. Commands run with the runtime role and `system_scope`."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.core.passwords import PasswordService, make_hasher
from app.db.session import make_engine, make_sessionmaker
from app.db.uow import make_uow_factory
from app.domain.errors import DomainError
from app.services.users import OnboardingService


async def create_org(args: argparse.Namespace, settings: Settings) -> int:
    # The password is read from the environment or a prompt, never from argv (visible in `ps` and shell history).
    password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
    engine = make_engine(settings.database_url, application_name="revops-cli")
    try:
        uow_factory = make_uow_factory(make_sessionmaker(engine))
        service = OnboardingService(
            uow_factory, PasswordService(make_hasher(fast_for_tests=settings.env == "test"))
        )
        org, admin = await service.create_org(
            name=args.name,
            legal_name=args.legal_name or args.name,
            state_code=args.state_code,
            gstin=args.gstin,
            admin_email=args.admin_email,
            admin_name=args.admin_name,
            admin_password=password,
        )
    except DomainError as exc:
        sys.stderr.write(f"error: {exc.message}\n")
        return 2
    finally:
        await engine.dispose()
    sys.stdout.write(f"created organization {org.id} with admin {admin.email}\n")
    return 0


async def seed_demo(args: argparse.Namespace, settings: Settings) -> int:
    from datetime import date

    from app.domain.ist import ist_date_of
    from app.seed.demo import LOGIN_DISABLED_HASH, generate
    from app.seed.loader import load_demo

    if settings.is_production:
        sys.stderr.write("error: seed-demo refuses to run in production\n")
        return 2
    if not settings.migration_database_url:
        sys.stderr.write("error: MIGRATION_DATABASE_URL (the owner role) is required for seeding\n")
        return 2
    password = os.environ.get("SEED_DEMO_PASSWORD")
    if args.no_login:
        password_hash = LOGIN_DISABLED_HASH
    elif password:
        password_hash = make_hasher(fast_for_tests=settings.env == "test").hash(password)
    else:
        sys.stderr.write(
            "error: set SEED_DEMO_PASSWORD, or pass --no-login to create users that cannot log in\n"
        )
        return 2
    today = date.fromisoformat(args.today) if args.today else ist_date_of(datetime.now(UTC))
    data = generate(args.seed, today, password_hash)
    try:
        counts = await load_demo(settings.migration_database_url, data, reset=args.reset)
    except Exception as exc:  # operator tool: show the reason, never a stack trace with credentials
        sys.stderr.write(
            f"error: seeding failed ({type(exc).__name__}); use --reset on a database that already has data\n"
        )
        return 1
    sys.stdout.write(f"seeded demo data for {today}: {json.dumps(counts, sort_keys=True)}\n")
    return 0


async def export_contract_cmd(args: argparse.Namespace, settings: Settings) -> int:
    from datetime import date

    from app.domain.ist import ist_date_of
    from app.main import create_app
    from app.seed.contract import export_contract

    if not settings.migration_database_url:
        sys.stderr.write("error: MIGRATION_DATABASE_URL (the owner role) is required\n")
        return 2
    today = date.fromisoformat(args.today) if args.today else ist_date_of(datetime.now(UTC))
    out = Path(args.out)
    openapi = create_app(settings).openapi()
    written = await export_contract(
        out, settings.migration_database_url, seed=args.seed, today=today,
        roles_sql=Path(args.roles_sql), openapi=openapi,
    )  # fmt: skip
    sys.stdout.write(f"contract written to {out}: {', '.join(written)}\n")
    return 0


def _write_text(path: Path, content: str) -> None:
    path.write_text(content)


async def dump_openapi(args: argparse.Namespace, settings: Settings) -> int:
    """Writes the OpenAPI document without touching the database (used by the frontend client drift check)."""
    from app.main import create_app

    _write_text(
        Path(args.out), json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n"
    )
    sys.stdout.write(f"openapi written to {args.out}\n")
    return 0


async def refresh_reporting(_args: argparse.Namespace, settings: Settings) -> int:
    from sqlalchemy import text

    engine = make_engine(settings.database_url, application_name="revops-refresh")
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT public.refresh_collections_mv()"))
    finally:
        await engine.dispose()
    sys.stdout.write("collections materialized view refreshed\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    co = sub.add_parser("create-org", help="create an organization and its first admin user")
    co.add_argument("--name", required=True)
    co.add_argument("--legal-name")
    co.add_argument("--state-code", required=True, help="two-digit GST state code, for example 33")
    co.add_argument("--gstin")
    co.add_argument("--admin-email", required=True)
    co.add_argument("--admin-name", required=True)
    sd = sub.add_parser(
        "seed-demo",
        help="load the deterministic demo dataset (owner connection; never in production)",
    )
    sd.add_argument("--reset", action="store_true", help="delete ALL existing data first")
    sd.add_argument("--seed", type=int, default=42)
    sd.add_argument("--today", help="YYYY-MM-DD, default: today in IST")
    sd.add_argument(
        "--no-login",
        action="store_true",
        help="users get an unusable password (used for the contract)",
    )
    ec = sub.add_parser(
        "export-contract", help="write the contract package (needs a seeded database)"
    )
    ec.add_argument("--out", default="../contract")
    ec.add_argument("--seed", type=int, default=42)
    ec.add_argument("--today")
    ec.add_argument("--roles-sql", default="../contract/roles.sql")
    do = sub.add_parser("dump-openapi", help="write openapi.json (no database needed)")
    do.add_argument("--out", default="openapi.json")
    sub.add_parser("refresh-reporting", help="refresh the collections materialized view")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    handlers = {
        "create-org": create_org, "seed-demo": seed_demo, "export-contract": export_contract_cmd,
        "refresh-reporting": refresh_reporting, "dump-openapi": dump_openapi,
    }  # fmt: skip
    if args.command in handlers:
        return asyncio.run(handlers[args.command](args, settings))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
