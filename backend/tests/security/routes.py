"""Route-table helpers for security tests. Uses FastAPI's public `iter_route_contexts` (routers are included lazily
in current FastAPI, so `app.routes` alone no longer lists the endpoints)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import iter_route_contexts

from app.core.permissions import Permission


@dataclass(frozen=True)
class RouteInfo:
    path: str
    methods: frozenset[str]
    permissions: frozenset[Permission]
    is_public: bool


def _walk(dep: Dependant) -> Iterator[object]:
    for sub in dep.dependencies:
        if sub.call is not None:
            yield sub.call
        yield from _walk(sub)


def application_routes(app: FastAPI) -> list[RouteInfo]:
    """Every endpoint that has dependency metadata (framework doc routes have none and are checked separately)."""
    found: list[RouteInfo] = []
    for ctx in iter_route_contexts(app.routes):
        dependant = getattr(ctx, "dependant", None)
        if dependant is None or ctx.path is None:
            continue
        permissions: set[Permission] = set()
        public = False
        for call in _walk(dependant):
            if hasattr(call, "__permission__"):
                permissions.add(call.__permission__)  # type: ignore[attr-defined]
            if getattr(call, "__public__", False):
                public = True
        methods = frozenset((ctx.methods or set()) - {"HEAD", "OPTIONS"})
        found.append(RouteInfo(ctx.path, methods, frozenset(permissions), public))
    return found


def framework_route_paths(app: FastAPI) -> set[str]:
    return {
        ctx.path
        for ctx in iter_route_contexts(app.routes)
        if getattr(ctx, "dependant", None) is None and ctx.path is not None
    }
