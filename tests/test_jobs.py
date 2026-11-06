"""Tests for the jobs API (`app/jobs.py`) and the job input rules (`app/models.py`).

Requests go through the real app with the in-memory test database (the
`client` fixture in conftest.py). `log_in_as("ayse")` signs Ayşe up and gives
back the header that proves she is logged in.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import func, select

from app import jobs
from app.models import Job, JobItem, JobState
from app.settings import settings

# A Turkish text with every special Turkish letter and an emoji with a skin
# tone, to prove texts are stored and returned exactly as sent.
TURKISH = "Çağrı İğdır'da ışık gördü, ÖŞÜ çok güzel 👍🏽"

# The only fields a job reply may contain, and those of a results row.
JOB_FIELDS = {
    "id",
    "task",
    "state",
    "total",
    "succeeded",
    "failed",
    "tokens_needed",
    "created_at",
    "started_at",
    "finished_at",
    "error_message",
}
RESULT_FIELDS = {"position", "original_text", "result", "state", "error_message"}


def send_job(client, headers, texts=None, task="normalise", key=None):
    """Send a job and return the reply. `key` adds an Idempotency-Key header."""
    if key is not None:
        headers = {**headers, "Idempotency-Key": key}
    body = {"task": task, "texts": ["merhaba"] if texts is None else texts}
    return client.post("/jobs", json=body, headers=headers)


def count_rows(session, table) -> int:
    """How many rows `table` has right now."""
    return session.exec(select(func.count()).select_from(table)).one()


def error_message(reply) -> str:
    """The `message` part of an error reply."""
    return reply.json()["error"]["message"]


# --- Sending a job ----------------------------------------------------------


def test_create_job_replies_202_with_ticket_and_location(client, log_in_as):
    headers = log_in_as("ayse")

    reply = send_job(client, headers, texts=["bir", "iki", "üç"])

    assert reply.status_code == 202
    body = reply.json()
    assert set(body) == JOB_FIELDS
    assert body["state"] == "queued"
    assert body["total"] == 3
    assert body["succeeded"] == body["failed"] == 0
    assert body["tokens_needed"] is None
    assert reply.headers["Location"] == f"/jobs/{body['id']}"


def test_create_job_saves_texts_numbered_and_waiting(client, log_in_as, session):
    headers = log_in_as("ayse")

    ticket = send_job(client, headers, texts=["bir", "iki", "üç"]).json()["id"]

    items = session.exec(
        select(JobItem)
        .where(JobItem.job_id == uuid.UUID(ticket))
        .order_by(JobItem.position)
    ).all()
    assert [(i.position, i.original_text, i.state) for i in items] == [
        (1, "bir", "waiting"),
        (2, "iki", "waiting"),
        (3, "üç", "waiting"),
    ]


@pytest.mark.parametrize("task", ["normalise", "count_tokens", "llm"])
def test_every_task_is_accepted(client, log_in_as, task):
    reply = send_job(client, log_in_as("ayse"), task=task)

    assert reply.status_code == 202
    assert reply.json()["task"] == task


def test_turkish_text_and_emoji_come_back_unchanged(client, log_in_as):
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, texts=[TURKISH]).json()["id"]

    results = client.get(f"/jobs/{ticket}/results", headers=headers).json()

    assert results[0]["original_text"] == TURKISH
    assert results[0]["original_text"].encode() == TURKISH.encode()


def test_line_breaks_tabs_and_windows_line_ends_are_allowed(client, log_in_as):
    headers = log_in_as("ayse")
    text = "satır bir\r\nsatır\tiki\nsatır üç"
    ticket = send_job(client, headers, texts=[text]).json()["id"]

    results = client.get(f"/jobs/{ticket}/results", headers=headers).json()

    assert results[0]["original_text"] == text


# --- Limits -----------------------------------------------------------------


@pytest.mark.parametrize("count", [1, settings.max_texts_per_job])
def test_text_count_at_the_limits_is_accepted(client, log_in_as, count):
    reply = send_job(client, log_in_as("ayse"), texts=["metin"] * count)

    assert reply.status_code == 202
    assert reply.json()["total"] == count


@pytest.mark.parametrize("count", [0, settings.max_texts_per_job + 1])
def test_text_count_outside_the_limits_is_refused(client, log_in_as, session, count):
    reply = send_job(client, log_in_as("ayse"), texts=["metin"] * count)

    assert reply.status_code == 422
    assert error_message(reply) == (
        f"texts: A job needs 1 to 1,000 texts; this one has {count:,}."
    )
    assert count_rows(session, Job) == 0


def test_text_at_the_character_limit_is_accepted(client, log_in_as):
    text = "ş" * settings.max_text_chars  # 2,000 characters, 4,000 bytes

    assert send_job(client, log_in_as("ayse"), texts=[text]).status_code == 202


def test_text_over_the_character_limit_is_refused(client, log_in_as):
    texts = ["kısa", "ş" * (settings.max_text_chars + 1)]

    reply = send_job(client, log_in_as("ayse"), texts=texts)

    assert reply.status_code == 422
    assert error_message(reply) == (
        "texts: Text 2 is longer than 2,000 characters (it has 2,001)."
    )


@pytest.mark.parametrize(
    ("bad_text", "reason"),
    [
        ("", "is empty or only spaces"),
        ("   ", "is empty or only spaces"),
        ("\n\t ", "is empty or only spaces"),
        ("null\x00karakter", "contains an invisible control character (U+0000)"),
        ("zil\x07sesi", "contains an invisible control character (U+0007)"),
        ("sil\x7f", "contains an invisible control character (U+007F)"),
        (42, "is not a piece of text"),
        (None, "is not a piece of text"),
    ],
)
def test_bad_text_refuses_the_whole_job_naming_its_position(
    client, log_in_as, session, bad_text, reason
):
    reply = send_job(client, log_in_as("ayse"), texts=["iyi", "iyi", bad_text])

    assert reply.status_code == 422
    assert error_message(reply).startswith(f"texts: Text 3 {reason}")
    assert count_rows(session, Job) == 0
    assert count_rows(session, JobItem) == 0


def test_only_the_first_bad_text_is_named(client, log_in_as):
    reply = send_job(client, log_in_as("ayse"), texts=["iyi", "", ""])

    assert error_message(reply) == "texts: Text 2 is empty or only spaces."


def test_lone_surrogate_is_refused(client, log_in_as):
    # "\ud800" is half of an emoji; only a hand-made request can contain it,
    # so the JSON is written by hand here.
    body = '{"task": "llm", "texts": ["yarım \\ud800 emoji"]}'
    headers = {**log_in_as("ayse"), "Content-Type": "application/json"}

    reply = client.post("/jobs", content=body, headers=headers)

    assert reply.status_code == 422
    assert "Text 1 contains a broken character" in error_message(reply)


@pytest.mark.parametrize(
    "body",
    [
        {"task": "translate", "texts": ["merhaba"]},  # unknown task
        {"texts": ["merhaba"]},  # no task
        {"task": "llm"},  # no texts
        {"task": "llm", "texts": "merhaba"},  # texts not a list
    ],
)
def test_malformed_job_gives_422(client, log_in_as, body):
    reply = client.post("/jobs", json=body, headers=log_in_as("ayse"))

    assert reply.status_code == 422
    assert reply.json()["error"]["code"] == "validation_error"


def test_job_and_texts_are_saved_all_or_nothing(
    client, log_in_as, session, monkeypatch
):
    # Make the third text point at a job that doesn't exist, so the database
    # refuses it in the middle of the save. Nothing at all may be left behind.
    real_job_item = jobs.JobItem

    def broken_job_item(**fields):
        if fields["position"] == 3:
            fields["job_id"] = uuid.uuid4()
        return real_job_item(**fields)

    monkeypatch.setattr(jobs, "JobItem", broken_job_item)

    reply = send_job(client, log_in_as("ayse"), texts=["bir", "iki", "üç", "dört"])

    assert reply.status_code == 500
    assert reply.json()["error"]["code"] == "internal_error"
    assert count_rows(session, Job) == 0
    assert count_rows(session, JobItem) == 0


# --- Double-click protection (Idempotency-Key) -----------------------------


def test_same_key_and_content_gives_same_ticket_and_one_job(client, log_in_as, session):
    headers = log_in_as("ayse")

    first = send_job(client, headers, texts=[TURKISH], key="tik-1")
    second = send_job(client, headers, texts=[TURKISH], key="tik-1")

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert second.headers["Location"] == first.headers["Location"]
    assert count_rows(session, Job) == 1
    assert count_rows(session, JobItem) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"texts": ["başka"]},  # different text
        {"texts": ["merhaba", "merhaba"]},  # extra text
        {"task": "llm"},  # different task
    ],
)
def test_same_key_with_different_content_gives_409(client, log_in_as, session, changes):
    headers = log_in_as("ayse")
    send_job(client, headers, key="tik-1")

    reply = send_job(client, headers, key="tik-1", **changes)

    assert reply.status_code == 409
    assert reply.json() == {
        "error": {
            "code": "idempotency_key_reused",
            "message": "This Idempotency-Key was already used for a different job.",
        }
    }
    assert count_rows(session, Job) == 1


def test_same_order_matters_for_same_content(client, log_in_as):
    headers = log_in_as("ayse")
    send_job(client, headers, texts=["bir", "iki"], key="tik-1")

    reply = send_job(client, headers, texts=["iki", "bir"], key="tik-1")

    assert reply.status_code == 409


def test_two_users_may_use_the_same_key(client, log_in_as, session):
    ayse = send_job(client, log_in_as("ayse"), key="tik-1")
    mehmet = send_job(client, log_in_as("mehmet"), key="tik-1")

    assert ayse.json()["id"] != mehmet.json()["id"]
    assert count_rows(session, Job) == 2


def test_jobs_without_a_key_are_never_merged(client, log_in_as, session):
    headers = log_in_as("ayse")

    first = send_job(client, headers)
    second = send_job(client, headers)

    assert first.json()["id"] != second.json()["id"]
    assert count_rows(session, Job) == 2


def test_two_requests_at_the_same_moment_make_one_job(
    client, log_in_as, session, monkeypatch
):
    # Pretend the second request looked for the key a split second before the
    # first one was saved: its lookup misses, so it tries to save a second job
    # and runs into the database's "one key per user" rule.
    headers = log_in_as("ayse")
    first = send_job(client, headers, key="tik-1")
    real_find = jobs.find_job_by_key
    lookups = []

    def find_that_misses_once(*args):
        lookups.append(args)
        return None if len(lookups) == 1 else real_find(*args)

    monkeypatch.setattr(jobs, "find_job_by_key", find_that_misses_once)

    second = send_job(client, headers, key="tik-1")

    assert len(lookups) == 2  # missed once, then found the first job
    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert count_rows(session, Job) == 1


def test_simultaneous_request_with_different_content_gives_409(
    client, log_in_as, monkeypatch
):
    headers = log_in_as("ayse")
    send_job(client, headers, key="tik-1")
    real_find = jobs.find_job_by_key
    lookups = []

    def find_that_misses_once(*args):
        lookups.append(args)
        return None if len(lookups) == 1 else real_find(*args)

    monkeypatch.setattr(jobs, "find_job_by_key", find_that_misses_once)

    reply = send_job(client, headers, texts=["başka"], key="tik-1")

    assert reply.status_code == 409


@pytest.mark.parametrize("key", ["", "k" * (settings.idempotency_key_max_length + 1)])
def test_empty_or_overlong_key_gives_422(client, log_in_as, key):
    assert send_job(client, log_in_as("ayse"), key=key).status_code == 422


def test_key_at_the_length_limit_is_accepted(client, log_in_as):
    key = "k" * settings.idempotency_key_max_length

    assert send_job(client, log_in_as("ayse"), key=key).status_code == 202


# --- Reading one job --------------------------------------------------------


def test_read_own_job(client, log_in_as):
    headers = log_in_as("ayse")
    sent = send_job(client, headers, texts=["bir", "iki"]).json()

    reply = client.get(f"/jobs/{sent['id']}", headers=headers)

    assert reply.status_code == 200
    assert reply.json() == sent


def test_other_users_missing_and_malformed_tickets_get_the_same_404(client, log_in_as):
    ayse_ticket = send_job(client, log_in_as("ayse")).json()["id"]
    mehmet = log_in_as("mehmet")

    for path in ["/jobs/{}", "/jobs/{}/results"]:
        others = client.get(path.format(ayse_ticket), headers=mehmet)
        missing = client.get(path.format(uuid.uuid4()), headers=mehmet)
        malformed = client.get(path.format("not-a-ticket"), headers=mehmet)

        assert others.status_code == missing.status_code == malformed.status_code
        assert others.status_code == 404
        assert others.json() == missing.json() == malformed.json()
        assert others.json() == {
            "error": {"code": "not_found", "message": "Job not found."}
        }


# --- Listing jobs -----------------------------------------------------------


def make_jobs(client, session, headers, count):
    """Send `count` jobs, give them creation times one minute apart, oldest
    first, and return their tickets in sending order."""
    tickets = [send_job(client, headers).json()["id"] for _ in range(count)]
    start = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    for minute, ticket in enumerate(tickets):
        job = session.get(Job, uuid.UUID(ticket))
        job.created_at = start + timedelta(minutes=minute)
        session.add(job)
    session.commit()
    return tickets


def test_list_shows_own_jobs_newest_first(client, log_in_as, session):
    headers = log_in_as("ayse")
    tickets = make_jobs(client, session, headers, 3)
    send_job(client, log_in_as("mehmet"))  # someone else's job

    reply = client.get("/jobs", headers=headers)

    assert reply.status_code == 200
    assert [job["id"] for job in reply.json()] == tickets[::-1]
    assert all(set(job) == JOB_FIELDS for job in reply.json())


def test_list_of_a_user_with_no_jobs_is_empty(client, log_in_as):
    send_job(client, log_in_as("ayse"))

    assert client.get("/jobs", headers=log_in_as("mehmet")).json() == []


def test_list_filters_by_state(client, log_in_as, session):
    headers = log_in_as("ayse")
    tickets = make_jobs(client, session, headers, 3)
    running = session.get(Job, uuid.UUID(tickets[1]))
    running.state = JobState.RUNNING
    session.add(running)
    session.commit()

    only_running = client.get("/jobs?status=running", headers=headers).json()
    only_queued = client.get("/jobs?status=queued", headers=headers).json()

    assert [job["id"] for job in only_running] == [tickets[1]]
    assert [job["id"] for job in only_queued] == [tickets[2], tickets[0]]


def test_list_pages_with_limit_and_offset(client, log_in_as, session):
    headers = log_in_as("ayse")
    newest_first = make_jobs(client, session, headers, 5)[::-1]

    page = client.get("/jobs?limit=2&offset=2", headers=headers).json()

    assert [job["id"] for job in page] == newest_first[2:4]


def test_list_default_page_size():
    # The default is read when the route is defined, so check the route itself
    # rather than creating 51 jobs.
    route = next(r for r in jobs.router.routes if r.name == "list_jobs")
    limit = next(p for p in route.dependant.query_params if p.name == "limit")

    assert limit.default == settings.jobs_page_default == 50


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        f"limit={settings.jobs_page_max + 1}",
        "offset=-1",
        "status=finished",  # not a job state
    ],
)
def test_bad_list_query_gives_422(client, log_in_as, query):
    reply = client.get(f"/jobs?{query}", headers=log_in_as("ayse"))

    assert reply.status_code == 422
    assert reply.json()["error"]["code"] == "validation_error"


def test_list_limit_at_the_maximum_is_accepted(client, log_in_as):
    reply = client.get(
        f"/jobs?limit={settings.jobs_page_max}", headers=log_in_as("ayse")
    )

    assert reply.status_code == 200


# --- Results ----------------------------------------------------------------


def test_results_one_row_per_text_in_order(client, log_in_as):
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, texts=["bir", TURKISH, "üç"]).json()["id"]

    reply = client.get(f"/jobs/{ticket}/results", headers=headers)

    assert reply.status_code == 200
    assert reply.json() == [
        {
            "position": position,
            "original_text": text,
            "result": None,
            "state": "waiting",
            "error_message": None,
        }
        for position, text in enumerate(["bir", TURKISH, "üç"], start=1)
    ]
    assert all(set(row) == RESULT_FIELDS for row in reply.json())


# --- Login and hidden fields ------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/jobs"),
        ("GET", "/jobs"),
        ("GET", f"/jobs/{uuid.uuid4()}"),
        ("GET", f"/jobs/{uuid.uuid4()}/results"),
    ],
)
def test_every_jobs_route_needs_login(client, method, path):
    reply = client.request(method, path, json={"task": "llm", "texts": ["a"]})

    assert reply.status_code == 401
    assert reply.json() == {
        "error": {"code": "not_authenticated", "message": "Not authenticated"}
    }


def test_internal_fields_never_appear_in_any_reply(client, log_in_as):
    headers = log_in_as("ayse")
    created = send_job(client, headers, key="gizli-anahtar")
    ticket = created.json()["id"]
    replies = [
        created,
        send_job(client, headers, key="gizli-anahtar"),  # repeat
        client.get(f"/jobs/{ticket}", headers=headers),
        client.get("/jobs", headers=headers),
        client.get(f"/jobs/{ticket}/results", headers=headers),
    ]

    for reply in replies:
        for hidden in [
            "owner_id",
            "claimed_by",
            "crash_count",
            "last_active_at",
            "idempotency_key",
            "gizli-anahtar",
            "hashed_password",
            "$argon2",
            "job_id",
            "tries",
        ]:
            assert hidden not in reply.text
