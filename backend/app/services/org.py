"""Organization settings."""

from __future__ import annotations

from app.core.context import AuthContext, RequestMeta
from app.db.models.organization import Organization
from app.db.uow import UnitOfWorkFactory
from app.domain.gstin import validate_gstin, validate_state_code
from app.repositories.audit import record_audit
from app.repositories.organizations import OrganizationRepository
from app.schemas.org import OrgPatch

_AUDITED = (
    "name",
    "legal_name",
    "gstin",
    "state_code",
    "udyam_category",
    "address",
    "default_terms_days",
)


def _snapshot(org: Organization) -> dict[str, object]:
    return {field: getattr(org, field) for field in _AUDITED}


class OrgService:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def get(self, ctx: AuthContext) -> Organization:
        async with self._uow_factory() as uow:
            return await OrganizationRepository(uow.session, ctx.org_id).get()

    async def update(self, ctx: AuthContext, patch: OrgPatch, meta: RequestMeta) -> Organization:
        changes = patch.model_dump(exclude_unset=True)
        async with self._uow_factory() as uow:
            org = await OrganizationRepository(uow.session, ctx.org_id).get(for_update=True)
            before = _snapshot(org)
            state = changes.get("state_code", org.state_code)
            validate_state_code(state)
            gstin = changes.get("gstin", org.gstin)
            if gstin:
                validate_gstin(gstin, state)
            for key, value in changes.items():
                if key in ("name", "legal_name", "state_code") and value is None:
                    continue
                setattr(org, key, value)
            await uow.session.flush()
            await uow.session.refresh(org)  # updated_at is a server-side expression after UPDATE
            record_audit(
                uow.session,
                org_id=ctx.org_id,
                action="org.update",
                entity_type="organization",
                entity_id=org.id,
                actor=ctx,
                before=before,
                after=_snapshot(org),
                meta=meta,
            )
            return org
