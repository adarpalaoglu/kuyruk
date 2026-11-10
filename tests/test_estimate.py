"""Tests for the cost estimate, `POST /jobs/estimate` (`app/jobs.py`, D119).

It counts a job's tokens exactly as the budget does when the job is sent,
checks the job with the same rules as `POST /jobs`, and saves nothing.
"""

from sqlmodel import Session, select

from app.budget import tokens_for_job
from app.models import Job
from app.settings import settings

ESTIMATE = "/jobs/estimate"


def test_estimate_counts_like_the_budget(client, log_in_as):
    headers = log_in_as("ayse")
    texts = ["Kargo çok hızlı geldi.", "Fena değil, iş görür."]
    reply = client.post(ESTIMATE, json={"task": "llm", "texts": texts}, headers=headers)
    assert reply.status_code == 200
    assert reply.json() == {
        "texts": 2,
        "tokens_needed": tokens_for_job(texts),
        "left_today": settings.daily_token_budget,
    }


def test_estimate_matches_what_sending_the_job_saves(client, log_in_as):
    headers = log_in_as("ayse")
    job = {"task": "llm", "texts": ["Yazın: ayse@example.com veya 0532 123 45 67"]}
    estimate = client.post(ESTIMATE, json=job, headers=headers).json()
    sent = client.post("/jobs", json=job, headers=headers).json()
    assert estimate["tokens_needed"] == sent["tokens_needed"]


def test_estimate_hides_emails_before_counting(client, log_in_as):
    headers = log_in_as("ayse")
    long_email = "a-very-long-address-" * 5 + "@example.com"
    reply = client.post(
        ESTIMATE, json={"task": "llm", "texts": [f"yaz {long_email}"]}, headers=headers
    )
    assert reply.json()["tokens_needed"] == tokens_for_job(["yaz [EMAIL]"])


def test_free_tasks_have_no_token_count(client, log_in_as):
    headers = log_in_as("ayse")
    for task in ("normalise", "count_tokens"):
        reply = client.post(
            ESTIMATE, json={"task": task, "texts": ["a", "b"]}, headers=headers
        )
        assert reply.json()["tokens_needed"] is None
        assert reply.json()["texts"] == 2


def test_left_today_drops_after_a_sent_job(client, log_in_as):
    headers = log_in_as("ayse")
    job = {"task": "llm", "texts": ["bir iki üç"]}
    sent = client.post("/jobs", json=job, headers=headers).json()
    left = client.post(ESTIMATE, json=job, headers=headers).json()["left_today"]
    assert left == settings.daily_token_budget - sent["tokens_needed"]


def test_left_today_never_goes_below_zero(client, log_in_as, monkeypatch):
    headers = log_in_as("ayse")
    job = {"task": "llm", "texts": ["bir iki üç dört beş"]}
    needed = tokens_for_job(job["texts"])
    monkeypatch.setattr(settings, "daily_token_budget", needed)
    client.post("/jobs", json=job, headers=headers)
    monkeypatch.setattr(settings, "daily_token_budget", needed - 1)
    reply = client.post(
        ESTIMATE, json={"task": "normalise", "texts": ["a"]}, headers=headers
    )
    assert reply.json()["left_today"] == 0


def test_estimate_saves_nothing(client, log_in_as, engine):
    headers = log_in_as("ayse")
    client.post(ESTIMATE, json={"task": "llm", "texts": ["a"]}, headers=headers)
    with Session(engine) as session:
        assert session.exec(select(Job)).all() == []


def test_estimate_needs_login(client):
    reply = client.post(ESTIMATE, json={"task": "llm", "texts": ["a"]})
    assert reply.status_code == 401
    assert reply.json()["error"]["code"]


def test_broken_job_gets_the_same_422_as_sending_it(client, log_in_as):
    headers = log_in_as("ayse")
    for job in (
        {"task": "llm", "texts": []},
        {"task": "llm", "texts": ["ok", ""]},
        {"task": "llm", "texts": ["x" * (settings.max_text_chars + 1)]},
        {"task": "translate", "texts": ["a"]},
    ):
        estimate = client.post(ESTIMATE, json=job, headers=headers)
        sent = client.post("/jobs", json=job, headers=headers)
        assert estimate.status_code == sent.status_code == 422
        assert estimate.json()["error"] == sent.json()["error"]


def test_job_bigger_than_the_whole_budget_gets_the_same_422(
    client, log_in_as, monkeypatch
):
    headers = log_in_as("ayse")
    monkeypatch.setattr(settings, "daily_token_budget", 1)
    job = {"task": "llm", "texts": ["bir iki üç dört beş altı"]}
    estimate = client.post(ESTIMATE, json=job, headers=headers)
    sent = client.post("/jobs", json=job, headers=headers)
    assert estimate.status_code == 422
    assert estimate.json()["error"] == sent.json()["error"]
    assert estimate.json()["error"]["code"] == "job_exceeds_daily_budget"
