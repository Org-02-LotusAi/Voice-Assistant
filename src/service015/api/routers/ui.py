"""Static Identity Workbench UI. The pages are public static files; each page signs the user in (session cookie + CSRF header)
or redirects to /login, and every data call goes through the authenticated public API."""

from __future__ import annotations

from importlib import resources
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

router = APIRouter(include_in_schema=False)

# Explicit allowlist: path -> (file in service015/ui, media type). Nothing else under ui/ is reachable.
_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/talk.html": ("talk.html", "text/html; charset=utf-8"),
    "/login": ("login.html", "text/html; charset=utf-8"),
    "/change-password": ("change-password.html", "text/html; charset=utf-8"),
    "/templates.html": ("templates.html", "text/html; charset=utf-8"),
    "/voices.html": ("voices.html", "text/html; charset=utf-8"),
    "/monitor.html": ("monitor.html", "text/html; charset=utf-8"),
    "/ui/common.js": ("common.js", "text/javascript; charset=utf-8"),
    "/ui/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/ui/talk.js": ("talk.js", "text/javascript; charset=utf-8"),
    "/ui/login.js": ("login.js", "text/javascript; charset=utf-8"),
    "/ui/change-password.js": ("change-password.js", "text/javascript; charset=utf-8"),
    "/ui/templates.js": ("templates.js", "text/javascript; charset=utf-8"),
    "/ui/voices.js": ("voices.js", "text/javascript; charset=utf-8"),
    "/ui/monitor.js": ("monitor.js", "text/javascript; charset=utf-8"),
    "/ui/style.css": ("style.css", "text/css; charset=utf-8"),
}


def content_security_policy(livekit_url: str) -> str:
    """CSP for the UI pages only. connect-src lists the configured LiveKit host in both its ws(s) and http(s) forms
    (livekit-client probes http(s)://host/rtc/validate); `ws:` / `wss:` stay allowed for any LiveKit deployment."""
    parts = urlsplit(livekit_url)
    hosts = []
    if parts.netloc:
        secure = parts.scheme in ("wss", "https")
        hosts = [f"{'wss' if secure else 'ws'}://{parts.netloc}", f"{'https' if secure else 'http'}://{parts.netloc}"]
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' https://cdn.jsdelivr.net",
            "style-src 'self'",
            "img-src 'self' data:",
            "connect-src " + " ".join(["'self'", "ws:", "wss:", *hosts]),
            "media-src 'self' blob:",
            "worker-src 'self' blob:",
            "object-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
        ]
    )


def _make_handler(filename: str, media_type: str):
    path = resources.files("service015").joinpath("ui", filename)

    async def handler(request: Request) -> Response:
        headers = {
            "Cache-Control": "no-cache",
            "Content-Security-Policy": content_security_policy(request.app.state.settings.livekit_client_url),
        }
        return FileResponse(str(path), media_type=media_type, headers=headers)

    return handler


for _route, (_name, _type) in _FILES.items():
    router.add_api_route(_route, _make_handler(_name, _type), methods=["GET"])
