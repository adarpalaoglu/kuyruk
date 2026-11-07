"""Kuyruk's settings: every adjustable value in one place.

Each value has a safe default here and can be changed without touching code by
setting an environment variable (a named value given to a program when it
starts) or by writing it in a local `.env` file. The variable name is the field
name in capitals, e.g. `database_url` is read from `DATABASE_URL`.
"""

from typing import Literal

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

    # The daily AI budget (spec 3.1, D11): how many tokens one user's `llm`
    # jobs may need per UTC day (the day starts at midnight world time, not
    # Turkish time). Cleaning and counting jobs are free.
    daily_token_budget: int = Field(default=200_000, ge=1)

    # Token counting (app/tokens.py). The encoding is the rulebook that chops
    # text into tokens. Its file is kept inside the repo (app/data/), so
    # counting never needs the internet (D45).
    token_encoding: str = "o200k_base"

    # Which AI the `llm` task uses: "ollama" (the real model on this Mac) or
    # "fake" (a stand-in for tests and experiments). Only app/ai.py reads this.
    # "fake" by default, so a fresh copy of Kuyruk works with nothing installed.
    ai_backend: Literal["ollama", "fake"] = "fake"

    # The real AI. Ollama is a program that runs AI models on this computer and
    # answers web requests at this address. The model tag gemma4:e4b was
    # installed on 6 Nov 2026 with digest eb2177adf764... (spec 2.4, D08).
    # Temperature 0 and a fixed seed make the same text give the same summary
    # almost every time (not always: see limits), so experiment runs are
    # comparable (D46). The timeout is how many seconds to wait for an answer
    # before counting it as a temporary error.
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_temperature: float = 0.0
    ollama_seed: int = 42
    ollama_timeout_seconds: float = 120.0

    # The fake AI (spec 2.4). It waits, then returns a fixed answer.
    # Wait mode "fixed" always waits fake_ai_wait_seconds; "replay" picks each
    # wait from real measured times in fake_ai_replay_file (made in Slice 10),
    # using fake_ai_seed so the same seed always gives the same waits.
    fake_ai_wait_mode: Literal["fixed", "replay"] = "fixed"
    fake_ai_wait_seconds: float = Field(default=1.0, ge=0)
    fake_ai_replay_file: str = "bench/data/ollama_times.csv"
    fake_ai_seed: int = 42
    fake_ai_answer: str = "Bu metin kısaca özetlenmiştir."
    # Failing on purpose: 0 = never, 1 = every call, N = every Nth call. A retry
    # is a new call, so with N above 1 a retried text can succeed. The kind says
    # whether the failure is temporary (worth retrying) or permanent.
    fake_ai_fail_every: int = Field(default=0, ge=0)
    fake_ai_fail_kind: Literal["temporary", "permanent"] = "temporary"

    # The worker (app/worker.py, spec section 7). How often it looks for a new
    # queued job, in seconds (D53).
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    # How many texts of one job are worked on at the same time (D28).
    texts_at_once: int = Field(default=4, ge=1)
    # Tries per text for temporary AI errors (D16). The wait before the second
    # try is retry_first_wait_seconds, and it doubles each time: 1 s, then 2 s.
    max_tries_per_text: int = Field(default=3, ge=1)
    retry_first_wait_seconds: float = Field(default=1.0, ge=0)
    # Stop the whole job ("AI unavailable") after this many texts fail in a row
    # (D18, D54).
    stop_after_failures_in_a_row: int = Field(default=10, ge=1)
    # How often a running job's last_active_at is refreshed, in seconds.
    heartbeat_seconds: float = Field(default=5.0, gt=0)
    # After a stop signal (Ctrl+C, or Docker stopping the container), how long
    # the worker waits for texts already with the AI before it exits (D55).
    # Docker kills a container 10 s after the stop signal, so this must stay
    # below Docker's stop grace time.
    shutdown_grace_seconds: float = Field(default=8.0, ge=0)

    # Live progress (GET /jobs/{ticket}/events, app/events.py, spec 6.1). How
    # often an open stream looks at its job in the database, in seconds (D63),
    # and after how many quiet seconds it sends a keep-alive line (D66).
    events_poll_seconds: float = Field(default=0.5, gt=0)
    events_keepalive_seconds: float = Field(default=15.0, gt=0)


# One shared copy, created once when the program starts, that the rest of the
# code imports.
settings = Settings()
