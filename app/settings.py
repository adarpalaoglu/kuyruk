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

    # Job limits (spec section 3, D10). A job holds 1 to 1,000 texts, and each
    # text holds 1 to 2,000 characters. Breaking any limit refuses the whole
    # job (D12).
    min_texts_per_job: int = 1
    max_texts_per_job: int = 1000
    max_text_chars: int = 2000

    # Paging for the job list (GET /jobs): how many jobs one page shows if the
    # client doesn't say, and the most it may ask for at once.
    jobs_page_default: int = 50
    jobs_page_max: int = 200

    # The longest Idempotency-Key (double-click protection label) accepted.
    idempotency_key_max_length: int = 255

    # The longest request ID (X-Request-ID header) Kuyruk will reuse from a
    # client. Longer or odd-looking ones are replaced with a fresh ID.
    request_id_max_length: int = 64

    # The largest request body accepted, in bytes: 25 MB. The largest valid
    # job is 1,000 texts x 2,000 characters. Sent as plain UTF-8 that is at
    # most about 8 MB (4 bytes per character), but many tools (e.g. Python's
    # json.dumps) write each emoji as a 12-byte escape code such as
    # "\ud83d\ude00", which makes it about 24 MB. 25 MB fits even that, so a
    # job that follows the text rules is never refused for its size, while
    # someone still can't send gigabytes.
    max_request_bytes: int = 25 * 1024 * 1024


# One shared copy, created once when the program starts, that the rest of the
# code imports.
settings = Settings()
