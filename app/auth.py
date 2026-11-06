"""Sign-up, log-in, and "who is making this request?" for Kuyruk's API.

- `POST /register` creates an account.
- `POST /token` logs in with the OAuth2 *password flow* (a standard: the
  client sends username and password as a form and gets back a pass, a JWT).
- `get_current_user` is a *dependency* (something a route asks for and FastAPI
  supplies): any route that needs a logged-in user asks for it, and requests
  without a valid pass are turned away with 401 before the route runs.

Hashing and passes themselves are in `app/security.py`.
"""

from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db import get_session
from app.models import Token, User, UserCreate, UserPublic
from app.security import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    read_access_token,
    verify_password,
)
from app.settings import settings

router = APIRouter(tags=["auth"])

# Tells FastAPI that passes arrive as "Authorization: Bearer <pass>" and that
# they are obtained from /token. This also adds the "Authorize" button to /docs.
# If the header is missing it answers 401 by itself.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Shorthand for "this route needs a database session".
SessionDep = Annotated[Session, Depends(get_session)]

# One message for both a wrong password and an unknown username, so the reply
# never reveals whether a username exists.
LOGIN_FAILED = "Incorrect username or password"


@router.post(
    "/register", response_model=UserPublic, status_code=status.HTTP_201_CREATED
)
def register(new_user: UserCreate, session: SessionDep) -> User:
    """Create an account and reply with its public details (201).

    The route returns the full `User` row, but `response_model=UserPublic`
    makes FastAPI send only id, username and created_at. A taken username
    gives 409 Conflict.

    It is a plain `def`, not `async def`: hashing is slow, CPU-heavy work, and
    FastAPI runs plain `def` routes on a separate thread so other requests are
    not frozen while it runs.
    """
    user = User(
        username=new_user.username,
        hashed_password=hash_password(new_user.password),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        # The database's "unique username" rule refused the row. Relying on
        # the database (instead of looking first) also catches two sign-ups
        # with the same name arriving at the same moment.
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already taken",
        ) from None
    session.refresh(user)
    return user


def authenticate_user(session: Session, username: str, password: str) -> User | None:
    """Return the user if the username and password match, otherwise None.

    For an unknown username the password is still checked, against a dummy
    hash, so a wrong username takes as long as a wrong password.
    """
    if len(password) > settings.password_max_length:
        # No account can have a password this long (sign-up refuses it), so
        # fail at once instead of running the slow check on a huge input.
        # Answering faster here reveals nothing about which usernames exist.
        return None
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None:
        verify_password(password, DUMMY_HASH)
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


@router.post("/token", response_model=Token)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep
) -> Token:
    """Log in: check username and password, and reply with a 30-minute pass.

    Usernames are stored in lowercase, so the typed name is lowercased too:
    "Adar" logs in as "adar". Any failure gives the same 401 reply.
    """
    user = authenticate_user(session, form.username.lower(), form.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=LOGIN_FAILED,
            # The standard way to say "log in with a Bearer pass".
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(user.id))


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], session: SessionDep
) -> User:
    """Return the logged-in user for this request, or answer 401.

    Refused: a pass that is broken, changed, signed with another key, or
    expired, and a pass for a user who no longer exists. (A missing pass is
    already refused by `oauth2_scheme`.)
    """
    not_logged_in = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not logged in or login expired",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        user_id = read_access_token(token)
    except jwt.InvalidTokenError:
        raise not_logged_in from None
    user = session.get(User, user_id)
    if user is None:
        raise not_logged_in
    return user
