"""Tests for `app/settings.py`: defaults, and values read from the environment.

`monkeypatch` is a pytest helper that changes something (here an environment
variable) for one test only and undoes it afterwards. `_env_file=None` makes
the settings ignore any local `.env` file, so the tests give the same result on
every machine.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import DEMO_JWT_SECRET, Settings


def test_default_app_name(monkeypatch):
    monkeypatch.delenv("APP_NAME", raising=False)

    assert Settings(_env_file=None).app_name == "Kuyruk"


def test_app_name_read_from_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Kuyruk Test")

    assert Settings(_env_file=None).app_name == "Kuyruk Test"


def test_default_database_is_local_sqlite_file(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert Settings(_env_file=None).database_url == "sqlite:///./kuyruk.db"


def test_database_url_read_from_environment(monkeypatch):
    url = "postgresql+psycopg://kuyruk:secret@localhost:5432/kuyruk"
    monkeypatch.setenv("DATABASE_URL", url)

    assert Settings(_env_file=None).database_url == url


def test_empty_value_means_use_the_default(monkeypatch):
    # An empty line such as "DATABASE_URL=" (as in .env.example) must not
    # replace the default with an empty address.
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("APP_NAME", "")

    settings = Settings(_env_file=None)

    assert settings.database_url == "sqlite:///./kuyruk.db"
    assert settings.app_name == "Kuyruk"


def test_jwt_secret_is_required(monkeypatch):
    # Without a secret Kuyruk must refuse to start, not use a guessable one.
    monkeypatch.delenv("JWT_SECRET", raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_short_jwt_secret_refused(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 31)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_jwt_secret_hidden_when_settings_are_printed(monkeypatch):
    secret = "a-secret-that-must-never-be-printed-0123"
    monkeypatch.setenv("JWT_SECRET", secret)

    settings = Settings(_env_file=None)

    assert secret not in repr(settings)
    assert secret not in str(settings.jwt_secret)
    assert settings.jwt_secret.get_secret_value() == secret


def test_auth_defaults_match_the_spec(monkeypatch):
    for name in [
        "ACCESS_TOKEN_MINUTES",
        "USERNAME_MIN_LENGTH",
        "USERNAME_MAX_LENGTH",
        "PASSWORD_MIN_LENGTH",
        "PASSWORD_MAX_LENGTH",
    ]:
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.access_token_minutes == 30
    assert (settings.username_min_length, settings.username_max_length) == (3, 30)
    assert (settings.password_min_length, settings.password_max_length) == (8, 128)


def test_default_ai_backend_is_the_fake(monkeypatch):
    monkeypatch.delenv("AI_BACKEND", raising=False)

    assert Settings(_env_file=None).ai_backend == "fake"


def test_unknown_ai_backend_is_refused(monkeypatch):
    monkeypatch.setenv("AI_BACKEND", "openai")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_demo_secret_refused_outside_demo_mode(monkeypatch):
    # The demo key is public, so real use with it must be impossible (D80).
    monkeypatch.setenv("JWT_SECRET", DEMO_JWT_SECRET)
    monkeypatch.setenv("DEMO_MODE", "false")

    with pytest.raises(ValidationError, match="public demo key"):
        Settings(_env_file=None)


def test_demo_secret_allowed_in_demo_mode(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", DEMO_JWT_SECRET)
    monkeypatch.setenv("DEMO_MODE", "true")

    assert Settings(_env_file=None).demo_mode is True


def test_compose_uses_exactly_the_demo_secret():
    # If compose.yaml's demo key ever drifted from DEMO_JWT_SECRET, the check
    # above would silently stop protecting it.
    compose = (Path(__file__).parent.parent / "compose.yaml").read_text()

    assert f"JWT_SECRET:-{DEMO_JWT_SECRET}}}" in compose


def test_worker_alive_file_is_off_by_default(monkeypatch):
    monkeypatch.delenv("WORKER_ALIVE_FILE", raising=False)
    monkeypatch.delenv("WORKER_ALIVE_MAX_AGE_SECONDS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.worker_alive_file == ""
    assert settings.worker_alive_max_age_seconds == 30.0


def test_settings_errors_never_show_the_secret():
    # A wrong setting stops Kuyruk with an error message, which may end up in
    # a log. It must not repeat any part of the secret it was given. (Values
    # passed directly, secret first: without hide_input_in_errors the message
    # would start with it.)
    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            jwt_secret="QQQQ-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx-ZZZZ",
            heartbeat_seconds=5,
            stale_after_seconds=1,
        )

    assert "QQQQ" not in str(error.value)
    assert "ZZZZ" not in str(error.value)
