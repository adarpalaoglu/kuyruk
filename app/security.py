"""Kuyruk's password and login-pass toolbox.

Two jobs live here, kept apart from the web routes so they can be tested alone:

- *Hashing* passwords: a hash is a one-way scramble. Kuyruk stores only the
  scramble; to check a password it scrambles the attempt and compares. We use
  Argon2, a hash designed to be slow and memory-hungry so that guessing
  millions of passwords from a stolen database is very expensive.
- Making and reading *JWTs* (JSON Web Tokens): a short, signed text that says
  "this is user 7, valid until 14:30". Anyone can read it, but only someone who
  knows JWT_SECRET can make one, and any change breaks its signature.

The routes that use these are in `app/auth.py`.
"""

from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.settings import settings

# The signing method for passes: HMAC with SHA-256, using one shared secret
# (JWT_SECRET). It is a fixed design choice, not an adjustable limit, so it
# lives here rather than in settings.
JWT_ALGORITHM = "HS256"

# pwdlib's recommended setup: Argon2 with its current safe default strength.
password_hash = PasswordHash.recommended()

# A hash of a password nobody uses. When someone logs in with a username that
# doesn't exist, we still check their password against this, so the slow
# Argon2 step runs either way. Both failures then take about the same time,
# and nobody can find out which usernames exist by timing the reply.
DUMMY_HASH = password_hash.hash("kuyruk-dummy-password")


def hash_password(password: str) -> str:
    """Return the Argon2 hash of `password`, ready to store in the database.

    Each call adds a fresh random "salt" (extra random input stored inside the
    hash), so two users with the same password still get different hashes.
    """
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """Return True if `password` matches the stored hash `hashed`."""
    return password_hash.verify(password, hashed)


def create_access_token(user_id: int) -> str:
    """Make a signed login pass for the user with id `user_id`.

    The pass holds two standard fields: "sub" (subject: who it's for, as text,
    because the JWT standard says so) and "exp" (expiry: when it stops
    working). The user's id is used rather than the username because the id
    never changes.
    """
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_minutes)
    claims = {"sub": str(user_id), "exp": expires_at}
    return jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=JWT_ALGORITHM
    )


def read_access_token(token: str) -> int:
    """Check a login pass and return the user id inside it.

    Raises `jwt.InvalidTokenError` if the pass is broken, signed with another
    key, changed, expired, or missing its fields. Only HS256 is accepted, so
    nobody can send a pass that claims "no signature needed".
    """
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[JWT_ALGORITHM],
        options={"require": ["exp", "sub"]},
    )
    try:
        return int(claims["sub"])
    except ValueError as error:
        # "sub" was not a number, so this pass wasn't made by Kuyruk.
        raise jwt.InvalidTokenError("Pass subject is not a user id") from error
