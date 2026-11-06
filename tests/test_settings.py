from app.settings import Settings


def test_default_app_name(monkeypatch):
    monkeypatch.delenv("APP_NAME", raising=False)

    assert Settings(_env_file=None).app_name == "Kuyruk"


def test_app_name_read_from_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Kuyruk Test")

    assert Settings(_env_file=None).app_name == "Kuyruk Test"
