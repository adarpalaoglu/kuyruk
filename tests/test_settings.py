"""Tests for `app/settings.py`: defaults, and values read from the environment.

`monkeypatch` is a pytest helper that changes something (here an environment
variable) for one test only and undoes it afterwards. `_env_file=None` makes
the settings ignore any local `.env` file, so the tests give the same result on
every machine.
"""

import pytest
from pydantic import ValidationError

from app.settings import Settings


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
