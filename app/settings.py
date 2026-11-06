"""Kuyruk's settings: every adjustable value in one place.

Each value has a safe default here and can be changed without touching code by
setting an environment variable (a named value given to a program when it
starts) or by writing it in a local `.env` file. The variable name is the field
name in capitals, e.g. `database_url` is read from `DATABASE_URL`.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All of Kuyruk's adjustable values, read from the environment or `.env`.

    It exists so that limits, addresses and secrets are never hard-coded: they
    can differ between a laptop, the tests and the Docker setup.
    """

    # Read a local `.env` file if there is one; ignore names we don't use, so a
    # shared `.env` (which also holds Postgres's own settings) doesn't break us.
    # Treat an empty value (e.g. "DATABASE_URL=" copied from .env.example) as
    # "not set", so the default below is used instead of an empty string.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True
    )

    # The name shown at the top of the automatic API documentation page.
    app_name: str = "Kuyruk"

    # Where the database lives, written as a URL (an address string). The
    # default is SQLite (a database stored in one ordinary file, here
    # `kuyruk.db`), which needs no server. Real use points this at Postgres,
    # e.g. postgresql+psycopg://kuyruk:PASSWORD@localhost:5432/kuyruk
    database_url: str = "sqlite:///./kuyruk.db"

    # The secret key that signs login passes (JWTs, see app/security.py). Anyone
    # who knows it could forge a pass for any user, so it lives only in `.env`.
    # It has no default on purpose: Kuyruk refuses to start without it rather
    # than run with a guessable key. At least 32 characters, so it can't be
    # guessed by trying many keys. SecretStr hides the value as "**********"
    # whenever the settings are printed or logged.
    jwt_secret: SecretStr = Field(min_length=32)

    # How long a login pass stays valid, in minutes. After that, log in again.
    access_token_minutes: int = 30

    # Allowed username and password lengths, checked on sign-up. The password
    # maximum stops someone sending a huge "password" that would keep the
    # (deliberately slow) password check busy for a long time.
    username_min_length: int = 3
    username_max_length: int = 30
    password_min_length: int = 8
    password_max_length: int = 128


# One shared copy, created once when the program starts, that the rest of the
# code imports.
settings = Settings()
