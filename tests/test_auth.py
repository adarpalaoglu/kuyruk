"""Tests for sign-up, log-in and `get_current_user` (`app/auth.py`, `app/security.py`).

Requests go through the real app with the in-memory test database (the
`client` fixture in conftest.py). `get_current_user` is tested through a tiny
extra route, `/whoami`, that exists only in these tests.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app import auth
from app.auth import get_current_user
from app.main import app
from app.models import User
from app.security import JWT_ALGORITHM, create_access_token, verify_password
from app.settings import settings

# A small separate app with one route that needs a logged-in user. It replies
# with the username, so tests can see which user the pass belonged to.
whoami_app = FastAPI()


@whoami_app.get("/whoami")
def whoami(user: Annotated[User, Depends(get_current_user)]) -> dict[str, str]:
    """Reply with the logged-in user's name (test-only route)."""
    return {"username": user.username}


@pytest.fixture
def whoami_client(client: TestClient) -> TestClient:
    """A client for `/whoami`, using the same test database as `client`."""
    # Share the real app's dependency swaps, so `/whoami` also gets sessions on
    # the test database. `client` clears them after the test.
    whoami_app.dependency_overrides = app.dependency_overrides
    return TestClient(whoami_app)


def register(client: TestClient, username: str = "ayse", password: str = "gizli1234"):
    """Send a sign-up request and return the reply."""
    return client.post("/register", json={"username": username, "password": password})


def log_in(client: TestClient, username: str = "ayse", password: str = "gizli1234"):
    """Send a log-in request (a form, as OAuth2 requires) and return the reply."""
    return client.post("/token", data={"username": username, "password": password})


def bearer(token: str) -> dict[str, str]:
    """The header that presents a login pass."""
    return {"Authorization": f"Bearer {token}"}


def signed_token(claims: dict, secret: str | None = None) -> str:
    """Make a pass with any contents, signed with Kuyruk's secret by default."""
    key = secret or settings.jwt_secret.get_secret_value()
    return jwt.encode(claims, key, algorithm=JWT_ALGORITHM)


def in_30_minutes() -> datetime:
    """An expiry time that hasn't passed yet."""
    return datetime.now(UTC) + timedelta(minutes=30)


# --- Sign-up ----------------------------------------------------------------


def test_register_creates_user_and_replies_public_fields(client):
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "username", "created_at"}
    assert body["username"] == "ayse"


def test_password_is_stored_only_as_a_hash(client, session):
    register(client, password="gizli1234")

    user = session.exec(select(User)).one()
    assert user.hashed_password != "gizli1234"
    assert user.hashed_password.startswith("$argon2")
    assert verify_password("gizli1234", user.hashed_password)


def test_duplicate_username_gives_409(client):
    register(client)

    response = register(client, password="baska5678")

    assert response.status_code == 409


def test_same_name_in_other_case_gives_409(client):
    register(client, username="adar")

    response = register(client, username="ADAR")

    assert response.status_code == 409


def test_username_stored_in_lowercase(client, session):
    response = register(client, username="Adar_99")

    assert response.json()["username"] == "adar_99"
    assert session.exec(select(User)).one().username == "adar_99"


@pytest.mark.parametrize(
    "username",
    [
        "ab",  # too short (minimum 3)
        "a" * 31,  # too long (maximum 30)
        "ad-ar",  # dash not allowed
        "ad ar",  # space not allowed
        "ayşe",  # Turkish letter
        "İrem",  # Turkish capital dotted I
        "ırmak",  # Turkish dotless i
    ],
)
def test_bad_username_gives_422(client, username):
    assert register(client, username=username).status_code == 422


@pytest.mark.parametrize("password", ["1234567", "x" * 129])
def test_bad_password_length_gives_422(client, password):
    # 7 characters is one too few; 129 is one too many.
    assert register(client, password=password).status_code == 422


@pytest.mark.parametrize("body", [{}, {"username": "ayse"}, {"password": "gizli1234"}])
def test_missing_fields_give_422(client, body):
    assert client.post("/register", json=body).status_code == 422


def test_limits_at_the_edges_are_accepted(client):
    assert register(client, username="abc", password="12345678").status_code == 201
    assert register(client, username="b" * 30, password="y" * 128).status_code == 201


# --- Log-in -----------------------------------------------------------------


def test_login_gives_a_pass_that_is_accepted(client, whoami_client):
    register(client)

    response = log_in(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    me = whoami_client.get("/whoami", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json() == {"username": "ayse"}


def test_login_ignores_username_case(client, whoami_client):
    register(client, username="Adar")

    response = log_in(client, username="ADAR")

    assert response.status_code == 200
    token = response.json()["access_token"]
    assert whoami_client.get("/whoami", headers=bearer(token)).json() == {
        "username": "adar"
    }


def test_pass_expires_after_the_configured_minutes(client):
    register(client)
    before = datetime.now(UTC)

    token = log_in(client).json()["access_token"]

    claims = jwt.decode(token, options={"verify_signature": False})
    expires = datetime.fromtimestamp(claims["exp"], UTC)
    minutes = settings.access_token_minutes
    assert before + timedelta(minutes=minutes, seconds=-5) <= expires
    assert expires <= datetime.now(UTC) + timedelta(minutes=minutes, seconds=5)


def test_wrong_password_and_unknown_user_give_the_same_401(client):
    register(client)

    wrong_password = log_in(client, password="yanlis999")
    unknown_user = log_in(client, username="mehmet")

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    assert wrong_password.json() == {
        "error": {"code": "login_failed", "message": "Incorrect username or password"}
    }


def test_unknown_user_still_runs_the_password_check(client, monkeypatch):
    # Count the slow password checks. If an unknown username skipped the check,
    # it would answer much faster, revealing that the name doesn't exist.
    checks = []

    def counting_verify(password: str, hashed: str) -> bool:
        checks.append(hashed)
        return verify_password(password, hashed)

    monkeypatch.setattr(auth, "verify_password", counting_verify)
    register(client)

    log_in(client, password="yanlis999")
    log_in(client, username="mehmet")

    assert len(checks) == 2
    assert checks[1] == auth.DUMMY_HASH


def test_overlong_login_password_refused_without_the_slow_check(client, monkeypatch):
    # A password longer than sign-up allows can't belong to anyone, so login
    # must refuse it at once instead of spending the slow check on it.
    checks = []

    def counting_verify(password: str, hashed: str) -> bool:
        checks.append(hashed)
        return verify_password(password, hashed)

    monkeypatch.setattr(auth, "verify_password", counting_verify)
    register(client)

    response = log_in(client, password="x" * (settings.password_max_length + 1))

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "login_failed", "message": "Incorrect username or password"}
    }
    assert checks == []


# --- get_current_user: refused passes ---------------------------------------


def test_missing_pass_gives_401(whoami_client):
    assert whoami_client.get("/whoami").status_code == 401


def test_garbage_pass_gives_401(whoami_client):
    response = whoami_client.get("/whoami", headers=bearer("not-a-real-pass"))

    assert response.status_code == 401


def test_expired_pass_gives_401(client, whoami_client):
    user_id = register(client).json()["id"]
    an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    token = signed_token({"sub": str(user_id), "exp": an_hour_ago})

    assert whoami_client.get("/whoami", headers=bearer(token)).status_code == 401


def test_tampered_pass_gives_401(client, whoami_client):
    # Mehmet takes his own valid pass and rewrites the middle part (the
    # readable contents) to say he is Ayşe, keeping his original signature.
    ayse_id = register(client, username="ayse").json()["id"]
    register(client, username="mehmet")
    header, _, signature = (
        log_in(client, username="mehmet").json()["access_token"].split(".")
    )
    fake_claims = {"sub": str(ayse_id), "exp": int(in_30_minutes().timestamp())}
    fake_middle = (
        base64.urlsafe_b64encode(json.dumps(fake_claims).encode()).rstrip(b"=").decode()
    )
    tampered = f"{header}.{fake_middle}.{signature}"

    assert whoami_client.get("/whoami", headers=bearer(tampered)).status_code == 401


def test_pass_signed_with_another_secret_gives_401(client, whoami_client):
    user_id = register(client).json()["id"]
    other_secret = "someone-elses-secret-also-32-characters-long"
    token = signed_token({"sub": str(user_id), "exp": in_30_minutes()}, other_secret)

    assert whoami_client.get("/whoami", headers=bearer(token)).status_code == 401


def test_pass_for_a_user_who_does_not_exist_gives_401(whoami_client):
    token = create_access_token(999)

    assert whoami_client.get("/whoami", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "not-a-number", "exp": in_30_minutes()},  # not a user id
        {"exp": in_30_minutes()},  # no "sub": doesn't say who it is for
        {"sub": "1"},  # no "exp": would never expire
    ],
)
def test_pass_with_bad_contents_gives_401(client, whoami_client, claims):
    register(client)  # user 1 exists, so only the pass's contents are wrong
    token = signed_token(claims)

    assert whoami_client.get("/whoami", headers=bearer(token)).status_code == 401


# --- hashed_password never leaves the server --------------------------------


def test_hash_never_appears_in_any_reply(client, session):
    # Check the raw reply text, not the parsed reply, so nothing can hide.
    replies = [
        register(client),
        register(client),  # the 409 reply
        log_in(client),
        log_in(client, password="yanlis999"),
    ]
    stored_hash = session.exec(select(User)).one().hashed_password

    for reply in replies:
        assert "hashed_password" not in reply.text
        assert "$argon2" not in reply.text
        assert stored_hash not in reply.text
        assert "gizli1234" not in reply.text
