"""Tests for the `/health` route in `app/main.py`.

TestClient sends pretend web requests straight to the app, without starting a
real server, so the test is fast and needs no network.
"""

from fastapi.testclient import TestClient

from app.main import app

# Created without a `with` block, so the app's start-up code (creating tables)
# does not run: `/health` doesn't need the database.
client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
