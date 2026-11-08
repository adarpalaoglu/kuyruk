"""Kuyruk's settings: every adjustable value in one place.

Each value has a safe default here and can be changed without touching code by
setting an environment variable (a named value given to a program when it
starts) or by writing it in a local `.env` file. The variable name is the field
name in capitals, e.g. `database_url` is read from `DATABASE_URL`.
"""

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The login-pass key that compose.yaml uses when there is no `.env`, so that
# `docker compose up` works on a clean machine (D80). It is public (it is in
# this file and on GitHub), so anyone could forge login passes with it. That is
# fine for a demo on one's own computer and never fine for real use: Kuyruk
# refuses to start with it unless DEMO_MODE is true (see the check below).
DEMO_JWT_SECRET = "demo-only-jwt-secret-public-not-for-real-use"


class Settings(BaseSettings):
    """All of Kuyruk's adjustable values, read from the environment or `.env`.

    It exists so that limits, addresses and secrets are never hard-coded: they
    can differ between a laptop, the tests and the Docker setup.
    """

    # Read a local `.env` file if there is one; ignore names we don't use, so a
    # shared `.env` (which also holds Postgres's own settings) doesn't break us.
    # Treat an empty value (e.g. "DATABASE_URL=" copied from .env.example) as
    # "not set", so the default below is used instead of an empty string.
    # hide_input_in_errors: when a setting is wrong, the error message must not
    # repeat the values it was given; otherwise it shows the start and end of
    # them, which can include part of JWT_SECRET or the database password.
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
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
    # wait from real measured times in fake_ai_replay_file, using fake_ai_seed
    # so the same seed always gives the same waits. The default file holds the
    # real AI's per-text times measured at 4 texts at once, the worker's
    # texts_at_once default, so the fake behaves like the real AI under real
    # load (Slice 10, D93).
    fake_ai_wait_mode: Literal["fixed", "replay"] = "fixed"
    fake_ai_wait_seconds: float = Field(default=1.0, ge=0)
    fake_ai_replay_file: str = "bench/data/ollama_times_at_4.csv"
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
    # Crash recovery (spec 7.1, D68-D70). A running job whose last heartbeat
    # is older than stale_after_seconds is *stuck*: its worker has died or
    # frozen, and another worker may take it over. A job may be taken over at
    # most max_crash_count times; the next time it is stuck, it fails.
    stale_after_seconds: float = Field(default=30.0, gt=0)
    max_crash_count: int = Field(default=3, ge=0)

    # Live progress (GET /jobs/{ticket}/events, app/events.py, spec 6.1). How
    # often an open stream looks at its job in the database, in seconds (D63),
    # and after how many quiet seconds it sends a keep-alive line (D66).
    events_poll_seconds: float = Field(default=0.5, gt=0)
    events_keepalive_seconds: float = Field(default=15.0, gt=0)

    # Demo mode (spec 8). When true, POST /demo/crash-worker is switched on,
    # so a worker crash can be caused on purpose and the recovery watched
    # (D74). Off by default: outside demo mode that address answers 404.
    demo_mode: bool = False

    # Experiment E1 (spec 9.1, D95). When true, the experiment-only addresses
    # under /experiments/e1/ are switched on: a slow step written the wrong
    # and the right way, to measure how much a wrong one freezes the API. Off
    # by default (404), and never allowed together with demo mode. The slow
    # step may wait at most E1_MAX_WAIT_SECONDS, so nobody can freeze the API
    # for long by accident.
    experiments_enabled: bool = False
    e1_max_wait_seconds: float = Field(default=10.0, gt=0)

    # The worker's "I'm alive" file (D79), for Docker's health check. When a
    # path is set, the worker touches (re-dates) the file on every pass of its
    # loop and at every heartbeat; `python -m app.worker --check-alive` then
    # says "healthy" only if the file is younger than the maximum age. Empty =
    # off (tests and runs on the Mac write no file); compose.yaml sets a path.
    worker_alive_file: str = ""
    worker_alive_max_age_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def check_stale_after_heartbeat(self) -> "Settings":
        """Refuse to start if a healthy job could look stuck (D68).

        A job is stuck when its heartbeat is older than stale_after_seconds.
        If that were not longer than the time between two heartbeats, every
        job would look stuck between two beats, and workers would take over
        jobs that are still being worked on.
        """
        if self.stale_after_seconds <= self.heartbeat_seconds:
            raise ValueError(
                "STALE_AFTER_SECONDS must be bigger than HEARTBEAT_SECONDS."
            )
        return self

    @model_validator(mode="after")
    def check_experiments_never_in_demo_mode(self) -> "Settings":
        """Refuse to start with the experiment addresses on in demo mode (D95).

        Some experiment addresses freeze the whole API on purpose (that is
        what E1 measures). A demo is for visitors, so they must never be
        reachable there.
        """
        if self.experiments_enabled and self.demo_mode:
            raise ValueError(
                "EXPERIMENTS_ENABLED and DEMO_MODE can't both be true: the "
                "experiment addresses freeze the API on purpose."
            )
        return self

    @model_validator(mode="after")
    def check_demo_secret_only_in_demo_mode(self) -> "Settings":
        """Refuse to start with the public demo login key outside demo mode.

        compose.yaml supplies DEMO_JWT_SECRET when there is no `.env`, so a
        visitor can try Kuyruk at once (D80). Because that key is public,
        anyone could forge a login pass with it; it is only acceptable while
        DEMO_MODE is on, which says "this is a demo, not real use".
        """
        if not self.demo_mode and self.jwt_secret.get_secret_value() == (
            DEMO_JWT_SECRET
        ):
            raise ValueError(
                "JWT_SECRET is the public demo key. Set a real JWT_SECRET in "
                ".env, or DEMO_MODE=true for a demo."
            )
        return self


# One shared copy, created once when the program starts, that the rest of the
# code imports.
settings = Settings()
