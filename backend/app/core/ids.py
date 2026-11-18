"""Identifier generation (02 section 1). Every primary key comes from `new_id()`."""

from __future__ import annotations

import uuid

import uuid_utils


def new_id() -> uuid.UUID:
    """Time-ordered UUIDv7 (Python 3.13 has no uuid7 of its own; see SPEC_ISSUES S4).

    Time ordering keeps b-tree inserts mostly append-only, which is why v7 is preferred over v4 for primary keys.
    Returned as a stdlib UUID so no third-party type leaks into the rest of the code.
    """
    return uuid.UUID(int=uuid_utils.uuid7().int)
