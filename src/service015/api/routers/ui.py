"""Static Identity Workbench UI (unauthenticated; the pages call the public API with the user's key)."""

from __future__ import annotations

from importlib import resources

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

router = APIRouter(include_in_schema=False)

# Explicit allowlist: path -> (file in service015/ui, media type). Nothing else under ui/ is reachable.
_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/talk.html": ("talk.html", "text/html; charset=utf-8"),
    "/ui/common.js": ("common.js", "text/javascript; charset=utf-8"),
    "/ui/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/ui/talk.js": ("talk.js", "text/javascript; charset=utf-8"),
    "/ui/style.css": ("style.css", "text/css; charset=utf-8"),
}


def _make_handler(filename: str, media_type: str):
    path = resources.files("service015").joinpath("ui", filename)

    async def handler() -> Response:
        return FileResponse(str(path), media_type=media_type, headers={"Cache-Control": "no-cache"})

    return handler


for _route, (_name, _type) in _FILES.items():
    router.add_api_route(_route, _make_handler(_name, _type), methods=["GET"])
