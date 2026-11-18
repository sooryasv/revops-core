"""User administration (admin only) and tenant onboarding."""

from __future__ import annotations

import uuid

from app.core.context import AuthContext, RequestMeta
from app.core.passwords import PasswordService
from app.db.models.organization import Organization
from app.db.models.user import User
from app.db.uow import UnitOfWorkFactory
from app.domain.errors import InvalidState
from app.domain.gstin import validate_gstin, validate_state_code
from app.repositories.audit import record_audit
from app.repositories.organizations import create_organization
from app.repositories.users import UserRepository
from app.schemas.users import UserCreate, UserPatch


class UserService:
    def __init__(self, uow_factory: UnitOfWorkFactory, passwords: PasswordService) -> None:
        self._uow_factory = uow_factory
        self._passwords = passwords

    async def list(self, ctx: AuthContext) -> list[User]:
        async with self._uow_factory() as uow:
            return await UserRepository(uow.session, ctx.org_id).list_all()

    async def create(self, ctx: AuthContext, data: UserCreate, meta: RequestMeta) -> User:
        password_hash = await self._passwords.hash(data.password)
        async with self._uow_factory() as uow:
            user = await UserRepository(uow.session, ctx.org_id).create(
                email=str(data.email).lower(),
                full_name=data.full_name,
                password_hash=password_hash,
                role=data.role,
            )
            record_audit(
                uow.session,
                org_id=ctx.org_id,
                action="user.create",
                entity_type="user",
                entity_id=user.id,
                actor=ctx,
                after={"email": user.email, "role": user.role},
                meta=meta,
            )
            return user

    async def update(
        self, ctx: AuthContext, user_id: uuid.UUID, patch: UserPatch, meta: RequestMeta
    ) -> User:
        changes = patch.model_dump(exclude_unset=True)
        new_hash = (
            await self._passwords.hash(changes["password"]) if changes.get("password") else None
        )
        async with self._uow_factory() as uow:
            repo = UserRepository(uow.session, ctx.org_id)
            user = await repo.get(user_id, for_update=True)
            before = {"full_name": user.full_name, "role": user.role, "is_active": user.is_active}
            new_role = changes.get("role", user.role)
            new_active = changes.get("is_active", user.is_active)
            loses_admin = (
                user.role == "admin" and user.is_active and (new_role != "admin" or not new_active)
            )
            if loses_admin and await repo.count_active_admins(excluding=user.id) == 0:
                raise InvalidState("An organization needs at least one active admin.")
            revoke = new_role != user.role or new_active != user.is_active or new_hash is not None
            if "full_name" in changes and changes["full_name"] is not None:
                user.full_name = changes["full_name"]
            user.role = new_role
            user.is_active = new_active
            if new_hash is not None:
                user.password_hash = new_hash
            if revoke:
                user.token_version += 1  # existing sessions and agent tokens stop validating
            await uow.session.flush()
            record_audit(
                uow.session,
                org_id=ctx.org_id,
                action="user.update",
                entity_type="user",
                entity_id=user.id,
                actor=ctx,
                before=before,
                after={
                    "full_name": user.full_name,
                    "role": user.role,
                    "is_active": user.is_active,
                    "password_changed": new_hash is not None,
                },
                meta=meta,
            )
            return user


class OnboardingService:
    """Creates a tenant and its first admin. Used by the `create-org` CLI only; there is no public signup (R1)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, passwords: PasswordService) -> None:
        self._uow_factory = uow_factory
        self._passwords = passwords

    async def create_org(
        self,
        *,
        name: str,
        legal_name: str,
        state_code: str,
        gstin: str | None,
        admin_email: str,
        admin_name: str,
        admin_password: str,
    ) -> tuple[Organization, User]:
        validate_state_code(state_code)
        if gstin:
            validate_gstin(gstin, state_code)
        password_hash = await self._passwords.hash(admin_password)
        async with self._uow_factory() as uow:
            org = await create_organization(
                uow.session, name=name, legal_name=legal_name, state_code=state_code, gstin=gstin
            )
            admin = await UserRepository(uow.session, org.id).create(
                email=admin_email.lower(),
                full_name=admin_name,
                password_hash=password_hash,
                role="admin",
            )
            record_audit(
                uow.session,
                org_id=org.id,
                action="org.create",
                entity_type="organization",
                entity_id=org.id,
                actor_type="system",
                after={"name": name, "admin_email": admin.email},
            )
            return org, admin
