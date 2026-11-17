"""Every route of the app with its full path, on old and new FastAPI.

FastAPI 0.16x keeps an included router as one entry in `app.routes` (its routes no
longer appear there with the prefix), so tests that walk the routes read them here.
CI and Railway install unpinned versions, so both shapes must work.
"""
from dataclasses import dataclass


@dataclass
class RouteInfo:
    path: str
    methods: set
    route: object      # APIRoute / APIWebSocketRoute / Route (for isinstance checks)
    dependant: object  # with the router-level dependencies, as FastAPI runs it


def app_routes(app) -> list[RouteInfo]:
    try:
        from fastapi.routing import iter_route_contexts
    except ImportError:
        iter_route_contexts = None
    if iter_route_contexts is None:
        return [RouteInfo(getattr(r, "path", ""), set(getattr(r, "methods", None) or ()), r,
                          getattr(r, "dependant", None)) for r in app.routes]
    out = []
    for c in iter_route_contexts(app.routes):
        eff = getattr(c, "_effective_route", None)
        out.append(RouteInfo(c.path, set(c.methods or ()), c.route,
                             getattr(eff, "dependant", None) or getattr(c.route, "dependant", None)))
    return out
