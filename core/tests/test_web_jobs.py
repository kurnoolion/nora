"""Tests for the NORA web job queue."""

from __future__ import annotations

import asyncio

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

from core.src.web.jobs import Job, JobQueue


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _event_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop
    loop.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_jobs.db"


@pytest.fixture
def queue(db_path):
    return JobQueue(db_path)


@pytest.fixture
def q(queue):
    """An initialized queue ready for use."""
    _run(queue.init_db())
    return queue


def test_init_db_creates_tables(queue, db_path):
    _run(queue.init_db())

    async def _check():
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            return [r[0] for r in await cursor.fetchall()]

    tables = _run(_check())
    assert "jobs" in tables
    assert "job_logs" in tables


def test_init_db_idempotent(queue):
    _run(queue.init_db())
    _run(queue.init_db())


def test_submit_creates_job(q):
    job = _run(q.submit(
        "pipeline",
        "alice",
        environment="test-env",
        stages=["extract", "profile"],
    ))
    assert isinstance(job, Job)
    assert job.job_type == "pipeline"
    assert job.status == "submitted"
    assert job.submitted_by == "alice"
    assert job.environment == "test-env"
    assert job.stages == ["extract", "profile"]
    assert job.progress == 0
    assert job.created_at.endswith("Z")
    assert len(job.id) == 36  # UUID format


def test_submit_query_job(q):
    job = _run(q.submit("query", "bob", query_text="What is VoLTE?"))
    assert job.job_type == "query"
    assert job.query_text == "What is VoLTE?"
    assert job.stages == []


def test_get_retrieves_submitted_job(q):
    job = _run(q.submit("pipeline", "alice", stages=["extract"]))
    fetched = _run(q.get(job.id))
    assert fetched is not None
    assert fetched.id == job.id
    assert fetched.job_type == "pipeline"
    assert fetched.submitted_by == "alice"
    assert fetched.stages == ["extract"]


def test_get_nonexistent_returns_none(q):
    result = _run(q.get("nonexistent-id"))
    assert result is None


def test_list_jobs_reverse_chronological(q):
    job1 = _run(q.submit("pipeline", "alice"))
    job2 = _run(q.submit("query", "bob"))
    job3 = _run(q.submit("eval", "carol"))

    jobs = _run(q.list_jobs())
    assert len(jobs) == 3
    assert jobs[0].id == job3.id
    assert jobs[1].id == job2.id
    assert jobs[2].id == job1.id


def test_list_jobs_with_status_filter(q):
    job1 = _run(q.submit("pipeline", "alice"))
    job2 = _run(q.submit("pipeline", "bob"))
    _run(q.update_status(job1.id, "running"))

    running = _run(q.list_jobs(status="running"))
    assert len(running) == 1
    assert running[0].id == job1.id

    submitted = _run(q.list_jobs(status="submitted"))
    assert len(submitted) == 1
    assert submitted[0].id == job2.id


def test_list_jobs_with_limit(q):
    for i in range(5):
        _run(q.submit("pipeline", f"user{i}"))

    jobs = _run(q.list_jobs(limit=3))
    assert len(jobs) == 3


def test_update_status(q):
    job = _run(q.submit("pipeline", "alice", stages=["extract", "profile"]))
    _run(q.update_status(job.id, "running", current_stage="extract", progress=25))

    updated = _run(q.get(job.id))
    assert updated.status == "running"
    assert updated.current_stage == "extract"
    assert updated.progress == 25
    assert updated.started_at is not None


def test_update_status_completed_sets_completed_at(q):
    job = _run(q.submit("pipeline", "alice"))
    _run(q.update_status(job.id, "running"))
    _run(q.update_status(
        job.id, "completed", progress=100, result_summary="All stages passed"
    ))

    updated = _run(q.get(job.id))
    assert updated.status == "completed"
    assert updated.completed_at is not None
    assert updated.progress == 100
    assert updated.result_summary == "All stages passed"


def test_update_status_failed_sets_error(q):
    job = _run(q.submit("pipeline", "alice"))
    _run(q.update_status(job.id, "failed", error_message="Extract stage crashed"))

    updated = _run(q.get(job.id))
    assert updated.status == "failed"
    assert updated.error_message == "Extract stage crashed"
    assert updated.completed_at is not None


def test_append_and_get_logs(q):
    job = _run(q.submit("pipeline", "alice"))

    _run(q.append_log(job.id, "Starting extract stage"))
    _run(q.append_log(job.id, "Processed 5 documents"))
    _run(q.append_log(job.id, "Extract complete"))

    logs = _run(q.get_logs(job.id))
    assert len(logs) == 3
    assert logs[0] == "Starting extract stage"
    assert logs[1] == "Processed 5 documents"
    assert logs[2] == "Extract complete"


def test_get_logs_after_line(q):
    job = _run(q.submit("pipeline", "alice"))

    _run(q.append_log(job.id, "Line 1"))
    _run(q.append_log(job.id, "Line 2"))
    _run(q.append_log(job.id, "Line 3"))
    _run(q.append_log(job.id, "Line 4"))

    logs = _run(q.get_logs(job.id, after_line=2))
    assert len(logs) == 2
    assert logs[0] == "Line 3"
    assert logs[1] == "Line 4"


def test_get_logs_after_line_all_seen(q):
    job = _run(q.submit("pipeline", "alice"))
    _run(q.append_log(job.id, "Line 1"))
    _run(q.append_log(job.id, "Line 2"))

    logs = _run(q.get_logs(job.id, after_line=2))
    assert logs == []


def test_get_includes_logs(q):
    job = _run(q.submit("pipeline", "alice"))
    _run(q.append_log(job.id, "hello"))

    fetched = _run(q.get(job.id))
    assert fetched.log_lines == ["hello"]


def test_cancel_running_job(q):
    job = _run(q.submit("pipeline", "alice"))
    _run(q.update_status(job.id, "running"))

    result = _run(q.cancel(job.id))
    assert result is True

    cancelled = _run(q.get(job.id))
    assert cancelled.status == "cancelled"
    assert cancelled.completed_at is not None


def test_cancel_submitted_job(q):
    job = _run(q.submit("pipeline", "alice"))
    result = _run(q.cancel(job.id))
    assert result is True

    cancelled = _run(q.get(job.id))
    assert cancelled.status == "cancelled"


def test_cancel_completed_job_returns_false(q):
    job = _run(q.submit("pipeline", "alice"))
    _run(q.update_status(job.id, "completed"))

    result = _run(q.cancel(job.id))
    assert result is False


def test_cancel_failed_job_returns_false(q):
    job = _run(q.submit("pipeline", "alice"))
    _run(q.update_status(job.id, "failed", error_message="boom"))

    result = _run(q.cancel(job.id))
    assert result is False


def test_cancel_nonexistent_job_returns_false(q):
    result = _run(q.cancel("does-not-exist"))
    assert result is False


def test_progress_tracking(q):
    job = _run(q.submit(
        "pipeline", "alice",
        stages=["extract", "profile", "parse", "resolve"],
    ))

    _run(q.update_status(job.id, "running", current_stage="extract", progress=0))
    _run(q.update_status(job.id, "running", current_stage="extract", progress=25))
    _run(q.update_status(job.id, "running", current_stage="profile", progress=50))
    _run(q.update_status(job.id, "running", current_stage="parse", progress=75))
    _run(q.update_status(job.id, "running", current_stage="resolve", progress=90))
    _run(q.update_status(job.id, "completed", progress=100))

    final = _run(q.get(job.id))
    assert final.status == "completed"
    assert final.progress == 100


def test_cleanup_old(q, db_path):
    job = _run(q.submit("pipeline", "alice"))

    async def _backdate():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "UPDATE jobs SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?",
                (job.id,),
            )
            await db.commit()

    _run(_backdate())

    deleted = _run(q.cleanup_old(days=30))
    assert deleted == 1

    remaining = _run(q.list_jobs())
    assert len(remaining) == 0


def test_cleanup_old_preserves_recent(q):
    _run(q.submit("pipeline", "alice"))

    deleted = _run(q.cleanup_old(days=30))
    assert deleted == 0

    remaining = _run(q.list_jobs())
    assert len(remaining) == 1
