import re

from fastapi.testclient import TestClient

from tests.conftest import make_app


PAGES = ("/", "/talk.html", "/login", "/change-password", "/templates.html", "/voices.html", "/monitor.html")


def _client(db_file, voice_stack):
    return TestClient(make_app(db_file, voice_stack))


def test_workbench_and_talk_served_without_auth(db_file, voice_stack):
    with _client(db_file, voice_stack) as c:
        for path, marker in (("/", "CREATE IDENTITY"), ("/talk.html", "livekit-client@")):
            r = c.get(path)
            assert r.status_code == 200, path
            assert r.headers["content-type"].startswith("text/html"), path
            assert marker in r.text, path


def test_ui_assets_served_and_referenced_files_exist(db_file, voice_stack):
    with _client(db_file, voice_stack) as c:
        for page in PAGES:
            html = c.get(page).text
            for ref in re.findall(r'(?:src|href)="(/ui/[^"]+)"', html):
                r = c.get(ref)
                assert r.status_code == 200, ref
        assert c.get("/ui/app.js").headers["content-type"].startswith("text/javascript")
        assert c.get("/ui/style.css").headers["content-type"].startswith("text/css")


def test_talk_pins_livekit_client_with_integrity(db_file, voice_stack):
    with _client(db_file, voice_stack) as c:
        tag = re.search(r"<script[^>]*livekit-client[^>]*>", c.get("/talk.html").text).group(0)
    assert re.search(r"livekit-client@\d+\.\d+\.\d+/dist/livekit-client\.umd\.js", tag)
    assert 'integrity="sha384-G/xxtkVytOx/ia9Q8MXxM+V0ohsaY1fZAgVP3iSGTPz4wJ0s3+ulJNKXh/gnzEDZ"' in tag
    assert 'crossorigin="anonymous"' in tag


def test_ui_responses_carry_csp_but_api_responses_do_not(db_file, voice_stack):
    with _client(db_file, voice_stack) as c:
        for path in (*PAGES, "/ui/app.js", "/ui/common.js"):
            csp = c.get(path).headers["content-security-policy"]
            assert "script-src 'self' https://cdn.jsdelivr.net" in csp
            assert "media-src 'self' blob:" in csp
            assert "connect-src 'self' ws: wss: ws://localhost:7880 http://localhost:7880" in csp
        assert "content-security-policy" not in c.get("/health").headers


def test_pages_have_no_inline_script_or_style(db_file, voice_stack):
    # The CSP forbids inline script/style; keep the markup compatible with it.
    with _client(db_file, voice_stack) as c:
        for page in PAGES:
            html = c.get(page).text
            assert not re.search(r"<script(?![^>]*src=)[^>]*>", html), page
            assert " style=" not in html and "<style" not in html, page
            assert not re.search(r"\son[a-z]+=", html), page


def test_ui_is_not_a_directory_listing_and_api_still_needs_auth(db_file, voice_stack):
    with _client(db_file, voice_stack) as c:
        assert c.get("/ui/").status_code == 404
        assert c.get("/identities").status_code == 401


def test_phase2_pages_served_without_auth_with_markers(db_file, voice_stack):
    markers = {
        "/login": 'id="password"',
        "/change-password": 'id="current"',
        "/templates.html": "Guardrails - always applied",
        "/voices.html": 'id="v-consent"',
        "/monitor.html": "Monitor (read-only)",
    }
    with _client(db_file, voice_stack) as c:
        for path, marker in markers.items():
            r = c.get(path)
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/html"), path
            assert marker in r.text, path
            assert "content-security-policy" in r.headers, path
        for js in ("login", "change-password", "templates", "voices", "monitor"):
            assert c.get(f"/ui/{js}.js").status_code == 200, js


def test_pages_use_session_csrf_and_no_innerhtml(db_file, voice_stack):
    with _client(db_file, voice_stack) as c:
        common = c.get("/ui/common.js").text
        assert "X-CSRF-Token" in common and "/auth/me" in common
        for js in ("common", "app", "talk", "login", "change-password", "templates", "voices", "monitor"):
            src = c.get(f"/ui/{js}.js").text
            assert "innerHTML" not in src and "outerHTML" not in src and "insertAdjacentHTML" not in src, js
            assert "confirm(" not in src and "alert(" not in src, js
        assert "ARCHIVE" in c.get("/").text and 'id="archiveConfirm"' in c.get("/").text
