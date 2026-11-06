"""Tests for `app/settings.py`: defaults, and values read from the environment.

`monkeypatch` is a pytest helper that changes something (here an environment
variable) for one test only and undoes it afterwards. `_env_file=None` makes
the settings ignore any local `.env` file, so the tests give the same result on
every machine.
"""

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
