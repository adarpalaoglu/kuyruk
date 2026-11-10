"""Tests for CORS (`app/main.py`, spec 6, D121): only the interface may call.

A browser asks the API first (a *preflight*, an OPTIONS request) before a
page from another address sends a request with a login header, and reads a
reply only if it carries `Access-Control-Allow-Origin` for that page's
address. The interface's address (WEB_ORIGIN) gets it; any other gets none.
"""

from app.settings import settings

ALLOWED = settings.web_origin
OTHER = "http://evil.example"


def preflight(client, origin, headers="authorization,content-type,idempotency-key"):
    """Send the browser's "may I?" question for a POST /jobs from `origin`."""
    return client.options(
        "/jobs",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": headers,
        },
    )


def test_preflight_from_the_interface_is_allowed(client):
    reply = preflight(client, ALLOWED)
    assert reply.status_code == 200
    assert reply.headers["access-control-allow-origin"] == ALLOWED
    allowed = reply.headers["access-control-allow-headers"].lower()
    for header in ("authorization", "content-type", "idempotency-key"):
        assert header in allowed


def test_preflight_from_another_site_is_refused_in_the_error_shape(client):
    reply = preflight(client, OTHER)
    assert reply.status_code == 400
    assert "access-control-allow-origin" not in reply.headers
    assert reply.json() == {
        "error": {"code": "cors_refused", "message": "Disallowed CORS origin."}
    }
    assert "x-request-id" in reply.headers


def test_a_header_the_interface_never_sends_is_refused(client):
    reply = preflight(client, ALLOWED, headers="x-something-else")
    assert reply.status_code == 400
    assert reply.json()["error"]["code"] == "cors_refused"
    assert "headers" in reply.json()["error"]["message"]


def test_replies_to_the_interface_carry_the_cors_headers(client):
    reply = client.get("/health", headers={"Origin": ALLOWED})
    assert reply.headers["access-control-allow-origin"] == ALLOWED
    exposed = reply.headers["access-control-expose-headers"].lower()
    for header in ("x-request-id", "retry-after", "location"):
        assert header in exposed


def test_replies_to_another_site_carry_no_cors_headers(client):
    reply = client.get("/health", headers={"Origin": OTHER})
    # The API still answers (CORS is the browser's rule), but without the
    # header, so a browser hides the reply from the other site's page.
    assert reply.status_code == 200
    assert "access-control-allow-origin" not in reply.headers


def test_error_replies_also_carry_the_cors_headers(client):
    # 401 from the login check: the interface must be able to read the
    # message ("log in again"), so the header must be there too.
    reply = client.get("/jobs", headers={"Origin": ALLOWED})
    assert reply.status_code == 401
    assert reply.headers["access-control-allow-origin"] == ALLOWED
    assert "x-request-id" in reply.headers


def test_too_large_body_reply_carries_the_cors_headers(client, monkeypatch):
    monkeypatch.setattr(settings, "max_request_bytes", 10)
    reply = client.post(
        "/register",
        content=b"x" * 100,
        headers={"Origin": ALLOWED, "Content-Type": "application/json"},
    )
    assert reply.status_code == 413
    assert reply.headers["access-control-allow-origin"] == ALLOWED
