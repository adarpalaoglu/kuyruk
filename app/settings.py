"""Kuyruk's settings: every adjustable value in one place.

Each value has a safe default here and can be changed without touching code by
setting an environment variable (a named value given to a program when it
starts) or by writing it in a local `.env` file. The variable name is the field
name in capitals, e.g. `database_url` is read from `DATABASE_URL`.
"""

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


# One shared copy, created once when the program starts, that the rest of the
# code imports.
settings = Settings()
