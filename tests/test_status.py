"""Tests for the public system status, `GET /status` (`app/status.py`, D117).

It needs no login, reports the database, the worker (from the age of its
"I'm alive" file, D118), the job counts, the AI and demo mode, and the limits
the interface shows. Being public, it must never show a user name or ticket.
"""

import os
import time

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session

from app.db import get_engine
from app.main import app
from app.models import Job, JobState, Task, User
from app.settings import settings


def add_job(engine, owner_id, state):
    """Save one job in `state` for user `owner_id`."""
    with Session(engine) as session:
        session.add(Job(owner_id=owner_id, task=Task.LLM, state=state, total=1))
        session.commit()


def test_status_needs_no_login_and_reports_every_part(client):
    reply = client.get("/status")
    assert reply.status_code == 200
    body = reply.json()
    assert body["api"] == "ok"
    assert body["database"] == "ok"
    assert body["running_jobs"] == 0
    assert body["jobs_waiting"] == 0
    assert body["demo_mode"] is settings.demo_mode
    assert set(body) == {
        "api",
        "database",
        "worker",
        "running_jobs",
        "jobs_waiting",
        "ai",
        "demo_mode",
        "limits",
    }
    assert "x-request-id" in reply.headers


def test_status_counts_running_and_waiting_jobs_of_everyone(client, engine):
    with Session(engine) as session:
        for name in ("ayse", "mehmet"):
            session.add(User(username=name, hashed_password="x"))
        session.commit()
    for owner, state in [
        (1, JobState.RUNNING),
        (2, JobState.QUEUED),
        (2, JobState.QUEUED),
        (1, JobState.DONE),
        (2, JobState.CANCELLED),
    ]:
        add_job(engine, owner, state)
    body = client.get("/status").json()
    assert body["running_jobs"] == 1
    assert body["jobs_waiting"] == 2


def test_status_never_shows_user_names_or_tickets(client, log_in_as):
    headers = log_in_as("ayse")
    ticket = client.post(
        "/jobs", json={"task": "normalise", "texts": ["a"]}, headers=headers
    ).json()["id"]
    text = client.get("/status").text
    assert "ayse" not in text
    assert ticket not in text


def test_status_says_database_down_when_it_does_not_answer(client):
    # An engine pointing at a folder that doesn't exist: every query fails.
    broken = create_engine("sqlite:////no/such/folder/kuyruk.db")
    app.dependency_overrides[get_engine] = lambda: broken
    body = client.get("/status").json()
    assert body["database"] == "down"
    assert body["running_jobs"] is None
    assert body["jobs_waiting"] is None
    # The API itself still answers.
    assert body["api"] == "ok"


def test_worker_is_unknown_without_an_alive_file_setting(client, monkeypatch):
    monkeypatch.setattr(settings, "worker_alive_file", "")
    assert client.get("/status").json()["worker"] == {
        "state": "unknown",
        "last_seen_seconds": None,
    }


def test_worker_is_unknown_when_the_file_does_not_exist(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "worker_alive_file", str(tmp_path / "missing"))
    assert client.get("/status").json()["worker"]["state"] == "unknown"


def test_worker_is_ok_while_its_alive_file_is_fresh(client, monkeypatch, tmp_path):
    alive = tmp_path / "alive"
    alive.touch()
    monkeypatch.setattr(settings, "worker_alive_file", str(alive))
    worker = client.get("/status").json()["worker"]
    assert worker["state"] == "ok"
    assert 0 <= worker["last_seen_seconds"] < 5


def test_worker_is_down_when_its_alive_file_is_too_old(client, monkeypatch, tmp_path):
    alive = tmp_path / "alive"
    alive.touch()
    # Date the file 100 seconds back, older than the 30-second limit.
    old = time.time() - 100
    os.utime(alive, (old, old))
    monkeypatch.setattr(settings, "worker_alive_file", str(alive))
    worker = client.get("/status").json()["worker"]
    assert worker["state"] == "down"
    assert worker["last_seen_seconds"] == pytest.approx(100, abs=5)


def test_a_file_dated_in_the_future_shows_age_zero(client, monkeypatch, tmp_path):
    alive = tmp_path / "alive"
    alive.touch()
    future = time.time() + 60
    os.utime(alive, (future, future))
    monkeypatch.setattr(settings, "worker_alive_file", str(alive))
    assert client.get("/status").json()["worker"]["last_seen_seconds"] == 0


@pytest.mark.parametrize(
    ("backend", "mode", "expected_mode"),
    [
        ("fake", "replay", "replay"),
        ("fake", "fixed", "fixed"),
        ("ollama", "replay", None),
    ],
)
def test_status_names_the_ai(client, monkeypatch, backend, mode, expected_mode):
    monkeypatch.setattr(settings, "ai_backend", backend)
    monkeypatch.setattr(settings, "fake_ai_wait_mode", mode)
    assert client.get("/status").json()["ai"] == {
        "backend": backend,
        "wait_mode": expected_mode,
        "model": settings.ollama_model,
    }


def test_status_reports_demo_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    assert client.get("/status").json()["demo_mode"] is True


def test_status_limits_follow_the_settings(client, monkeypatch):
    monkeypatch.setattr(settings, "max_texts_per_job", 7)
    monkeypatch.setattr(settings, "password_min_length", 12)
    limits = client.get("/status").json()["limits"]
    assert limits["max_texts_per_job"] == 7
    assert limits["password_min_length"] == 12
    assert limits["max_text_chars"] == settings.max_text_chars
    assert limits["texts_at_once"] == settings.texts_at_once
    assert limits["stale_after_seconds"] == settings.stale_after_seconds
