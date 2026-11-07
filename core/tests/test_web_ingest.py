"""Tests for the Ingest page and API (core/src/web/routes/ingest.py).

The jobs database is a temp file set on `app.state`; the store's healthz
is replaced by a stub of `served_cells`. The token gate is exercised
with team mode off and on, and with the ingest and the admin cookie.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")
from fastapi.testclient import TestClient  # noqa: E402

from core.src.web import team_mode as tm  # noqa: E402
from core.src.web.app import app  # noqa: E402
from core.src.web.jobs import JobQueue  # noqa: E402
from core.src.web.path_mapper import PathMapper  # noqa: E402
from core.src.web.routes import ingest as ingest_mod  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture()
def web(tmp_path: Path, monkeypatch):
    q = JobQueue(tmp_path / "jobs.db")
    _run(q.init_db())
    app.state.job_queue = q
    app.state.path_mapper = PathMapper([])
    monkeypatch.setattr(tm, "TEAM_MODE", False)
    monkeypatch.setattr(tm, "ADMIN_TOKEN", "adm")
    monkeypatch.setattr(tm, "INGEST_TOKEN", "ing")
    cells = {"value": ["MNO-A/Jan2031", "MNO-B/Jan2031"]}

    async def _cells():
        return cells["value"]
    monkeypatch.setattr(ingest_mod, "served_cells", _cells)
    return {"q": q, "cells": cells}


def _client(cookie: str | None = "ing", admin: bool = False) -> TestClient:
    cookies = {}
    if cookie:
        cookies[tm.INGEST_COOKIE] = cookie
    if admin:
        cookies[tm.ADMIN_COOKIE] = "adm"
    return TestClient(app, cookies=cookies)


JSON = {"accept": "application/json", "content-type": "application/json"}
REQ = {"source": "\\\\SERVER\\OADocs\\MNO-A\\Apr2031", "mno": "MNO-A", "release": "Apr2031",
       "submitted_by": "req team"}


# -- the gate --------------------------------------------------------------------------

def test_locked_without_cookie(web):
    c = _client(cookie=None)
    r = c.get("/ingest")
    assert r.status_code == 200 and "needs the ingest token" in r.text and "New ingest" not in r.text
    assert c.get("/api/ingest", headers=JSON).status_code == 403
    assert c.post("/api/ingest", headers=JSON, content=json.dumps(REQ)).status_code == 403
    # a form post without the cookie is sent back to the page
    r = c.post("/api/ingest", data=REQ, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/ingest")


def test_wrong_cookie_is_locked(web):
    assert _client(cookie="nope").get("/api/ingest", headers=JSON).status_code == 403


def test_admin_cookie_passes(web):
    assert _client(cookie=None, admin=True).get("/api/ingest", headers=JSON).status_code == 200


def test_unlock_sets_cookie_and_wrong_token_does_not(web):
    c = TestClient(app)
    r = c.get("/ingest-unlock?token=ing", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].endswith("/ingest")
    assert "nora_ingest=ing" in r.headers.get("set-cookie", "") and "HttpOnly" in r.headers["set-cookie"]
    r = c.get("/ingest", follow_redirects=False)
    assert "New ingest" in r.text
    c2 = TestClient(app)
    r = c2.get("/ingest-unlock?token=wrong", follow_redirects=False)
    assert r.status_code == 302 and "set-cookie" not in r.headers
    assert "needs the ingest token" in c2.get("/ingest").text


def test_no_token_configured_closes_the_surface(web, monkeypatch):
    monkeypatch.setattr(tm, "INGEST_TOKEN", "")
    c = TestClient(app, cookies={tm.INGEST_COOKIE: ""})
    assert c.get("/api/ingest", headers=JSON).status_code == 403
    assert c.get("/ingest-unlock?token=", follow_redirects=False).headers.get("set-cookie") is None


def test_team_mode_on_gated_member_reaches_page_with_cookie(web, monkeypatch):
    monkeypatch.setattr(tm, "TEAM_MODE", True)
    # no cookie at all: the middleware lets /ingest through (allowlisted) and the route says locked
    r = TestClient(app).get("/ingest", follow_redirects=False)
    assert r.status_code == 200 and "needs the ingest token" in r.text
    # the ingest cookie alone (not admin) opens the page and the API under the gate
    c = _client()
    assert "New ingest" in c.get("/ingest").text
    assert c.get("/api/ingest", headers=JSON).status_code == 200
    # the unlock route is reachable under the gate
    assert TestClient(app).get("/ingest-unlock?token=ing", follow_redirects=False).status_code == 302
    # and the rest of the app is still gated for that member
    assert c.get("/jobs", follow_redirects=False).status_code == 302


# -- submit ----------------------------------------------------------------------------------

def test_submit_json_creates_ingest_and_job(web):
    c = _client()
    r = c.post("/api/ingest", headers=JSON, content=json.dumps(REQ))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["state"] == "queued"
    ing = _run(web["q"].get_ingest(body["ingest_id"]))
    job = _run(web["q"].get_meta(body["job_id"]))
    assert ing.mno == "MNO-A" and ing.release == "Apr2031" and ing.job_id == job.id
    assert ing.source == REQ["source"] and ing.submitted_by == "req team" and ing.state == "queued"
    assert job.job_type == "ingest" and job.params == {"ingest_id": ing.id} and job.status == "submitted"
    assert job.stages[0] == "documents" and job.stages[-1] == "gate"
    # list and get
    lst = c.get("/api/ingest", headers=JSON).json()
    assert [i["id"] for i in lst["ingests"]] == [ing.id] and lst["runner"]["state"] == "never"
    got = c.get(f"/api/ingest/{ing.id}", headers=JSON).json()
    assert got["ingest"]["can_cancel"] and not got["ingest"]["can_flip"] and got["job"]["status"] == "submitted"
    assert c.get("/api/ingest/nope", headers=JSON).status_code == 404


def test_submit_form_redirects_to_detail_and_normalises(web):
    c = _client()
    r = c.post("/api/ingest", data={**REQ, "mno": "mno-a", "release": "APR2031"}, follow_redirects=False)
    assert r.status_code == 303
    ingest_id = r.headers["location"].rsplit("/", 1)[1]
    ing = _run(web["q"].get_ingest(ingest_id))
    assert ing.mno == "MNO-A" and ing.release == "Apr2031"
    page = c.get(f"/ingest/{ingest_id}")
    assert page.status_code == 200 and "MNO-A Apr2031" in page.text and "Cancel" in page.text
    assert "1. documents" in page.text and "12. gate" in page.text


@pytest.mark.parametrize("field,value,needle", [
    ("mno", "mno a", "MNO must be"),
    ("release", "April2031", "MMMYYYY"),
    ("release", "Xyz2031", "MMMYYYY"),
    ("source", "C:\\docs\\x", "shared-folder path"),
    ("source", "/mnt/x", "shared-folder path"),
    ("mno", "MNO-Z", "not a served MNO"),
])
def test_submit_refusals(web, field, value, needle):
    c = _client()
    r = c.post("/api/ingest", headers=JSON, content=json.dumps({**REQ, field: value}))
    assert r.status_code == 400 and needle in r.json()["error"], r.text
    assert _run(web["q"].list_ingests()) == []


def test_submit_refuses_a_served_cell_and_a_taken_cell_but_queues_a_second_run(web):
    c = _client()
    r = c.post("/api/ingest", headers=JSON, content=json.dumps({**REQ, "release": "Jan2031"}))
    assert r.status_code == 400 and "already served" in r.json()["error"]
    first = c.post("/api/ingest", headers=JSON, content=json.dumps(REQ))
    assert first.status_code == 202 and first.json()["queue_position"] == 1
    second = c.post("/api/ingest", headers=JSON, content=json.dumps({**REQ, "release": "Jul2031"}))
    assert second.status_code == 202 and second.json()["queue_position"] == 2
    r = c.post("/api/ingest", headers=JSON, content=json.dumps(REQ))
    assert r.status_code == 400 and "already queued" in r.json()["error"]
    lst = c.get("/api/ingest", headers=JSON).json()
    positions = {i["release"]: i["queue_position"] for i in lst["ingests"]}
    assert positions == {"Apr2031": 1, "Jul2031": 2} and lst["blocking"] == []
    page = c.get("/ingest").text
    assert "queued #1" in page and "queued #2" in page
    assert "queued #2" in c.get(f"/ingest/{second.json()['ingest_id']}").text
    # the runner takes them in order: the oldest job first
    from core.src.ingest_runner.db import RunnerDB
    db = RunnerDB(web["q"]._db_path)
    assert db.claim_next("r")["id"] == first.json()["job_id"]


def test_submit_refused_when_store_unreachable(web):
    web["cells"]["value"] = None
    c = _client()
    r = c.post("/api/ingest", headers=JSON, content=json.dumps(REQ))
    assert r.status_code == 400 and "cannot be asked" in r.json()["error"]
    page = c.get("/ingest")
    assert "cannot be asked" in page.text and 'disabled' in page.text


def test_form_refusal_redirects_with_the_reason(web):
    c = _client()
    r = c.post("/api/ingest", data={**REQ, "release": "bad"}, follow_redirects=False)
    assert r.status_code == 303 and "/ingest?error=" in r.headers["location"]
    page = c.get(r.headers["location"])
    assert "MMMYYYY" in page.text


# -- actions ---------------------------------------------------------------------------------

def _ingest(web, state: str, **cols) -> str:
    q = web["q"]
    ing = _run(q.create_ingest(mno="MNO-A", release="Apr2031", source="\\\\S\\x", submitted_by="t"))
    _run(q.update_ingest(ing.id, state=state, **cols))
    return ing.id


def test_flip_only_when_staged_and_passed(web):
    c = _client()
    staged_fail = _ingest(web, "staged", verdict="fail", label="L1")
    r = c.post(f"/api/ingest/{staged_fail}/flip", headers=JSON)
    assert r.status_code == 409 and "not servable" in r.json()["error"]
    page = c.get(f"/ingest/{staged_fail}")
    assert "Serve" not in page.text.split("All ingests")[1].split("<div class=\"row")[0]
    staged_pass = _ingest(web, "staged", verdict="pass", label="L2")
    page = c.get(f"/ingest/{staged_pass}")
    assert ">Serve" in page.text and "Abandon" in page.text
    r = c.post(f"/api/ingest/{staged_pass}/flip", headers=JSON)
    assert r.status_code == 202
    job = _run(web["q"].get_meta(r.json()["job_id"]))
    assert job.job_type == "ingest-flip" and job.params == {"ingest_id": staged_pass}
    assert _run(web["q"].get_ingest(staged_pass)).job_id == job.id


def test_rollback_retry_abandon_cancel(web):
    c = _client()
    served = _ingest(web, "served", verdict="pass", label="L", previous_label="P")
    assert ">Roll back" in c.get(f"/ingest/{served}").text
    r = c.post(f"/api/ingest/{served}/rollback", headers=JSON)
    assert r.status_code == 202 and _run(web["q"].get_meta(r.json()["job_id"])).job_type == "ingest-rollback"
    assert c.post(f"/api/ingest/{served}/abandon", headers=JSON).status_code == 409
    failed = _ingest(web, "failed", stage="verify", build="B", label="B")
    page = c.get(f"/ingest/{failed}")
    assert "Retry from enrich" in page.text and "Abandon" in page.text
    r = c.post(f"/api/ingest/{failed}/retry", headers=JSON)
    assert r.status_code == 202
    job = _run(web["q"].get_meta(r.json()["job_id"]))
    assert job.job_type == "ingest" and job.params == {"ingest_id": failed, "retry_from": "enrich"}
    ing = _run(web["q"].get_ingest(failed))
    assert ing.state == "queued" and ing.job_id == job.id
    # cancel a queued one: the job is cancelled and the ingest with it
    r = c.post(f"/api/ingest/{failed}/cancel", headers=JSON)
    assert r.status_code == 202
    assert _run(web["q"].get_meta(job.id)).status == "cancelled"
    assert _run(web["q"].get_ingest(failed)).state == "cancelled"
    # abandon a cancelled one
    r = c.post(f"/api/ingest/{failed}/abandon", headers=JSON)
    assert r.status_code == 202 and _run(web["q"].get_meta(r.json()["job_id"])).job_type == "ingest-abandon"
    # form actions redirect back to the detail page
    r = c.post(f"/api/ingest/{served}/flip", data={}, follow_redirects=False)
    assert r.status_code == 303 and f"/ingest/{served}?error=" in r.headers["location"]
    assert c.post("/api/ingest/nope/flip", headers=JSON).status_code == 404


def test_actions_need_the_cookie(web):
    served = _ingest(web, "served", verdict="pass", previous_label="P")
    assert _client(cookie=None).post(f"/api/ingest/{served}/rollback", headers=JSON).status_code == 403
    assert _run(web["q"].list_jobs()) == []


def test_runner_status_on_page(web):
    import sqlite3
    from core.src.ingest_runner.db import RunnerDB
    db = RunnerDB(web["q"]._db_path)
    db.seen("host-1", None)
    c = _client()
    assert "host-1 idle" in c.get("/ingest").text
    db.seen("host-1", "job-x")
    assert c.get("/api/ingest", headers=JSON).json()["runner"]["state"] == "busy"
    con = sqlite3.connect(web["q"]._db_path)
    con.execute("UPDATE runners SET seen_at = '2020-01-01T00:00:00.000000Z'")
    con.commit()
    assert "not seen for" in c.get("/ingest").text


def test_sidebar_shows_ingest_for_token_holders_only_under_the_gate(web, monkeypatch):
    monkeypatch.setattr(tm, "TEAM_MODE", True)
    assert 'title="Ingest a release"' not in TestClient(app).get("/test").text
    assert 'title="Ingest a release"' in _client().get("/test").text
    monkeypatch.setattr(tm, "TEAM_MODE", False)
    assert 'title="Ingest a release"' in TestClient(app).get("/ingest").text


def test_submit_with_callback_url(web):
    c = _client()
    r = c.post("/api/ingest", headers=JSON, content=json.dumps({**REQ, "callback_url": "ftp://x/y"}))
    assert r.status_code == 400 and "callback_url" in r.json()["error"]
    r = c.post("/api/ingest", headers=JSON,
               content=json.dumps({**REQ, "callback_url": "https://hooks.example.test/nora"}))
    assert r.status_code == 202
    ing = _run(web["q"].get_ingest(r.json()["ingest_id"]))
    assert ing.callback_url == "https://hooks.example.test/nora"
    assert c.get(f"/api/ingest/{ing.id}", headers=JSON).json()["ingest"]["callback_url"] == ing.callback_url


def test_events_stream(web):
    c = _client()
    r = c.post("/api/ingest", headers=JSON, content=json.dumps(REQ))
    ingest_id, job_id = r.json()["ingest_id"], r.json()["job_id"]
    # the runner's side: the job ran and the ingest is staged
    _run(web["q"].update_status(job_id, "completed"))
    _run(web["q"].update_ingest(ingest_id, state="staged", verdict="pass", label="L"))
    events = []
    with c.stream("GET", f"/api/ingest/{ingest_id}/events") as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("event: "):
                events.append([line[7:], None])
            elif line.startswith("data: ") and events:
                events[-1][1] = json.loads(line[6:])
    names = [e[0] for e in events]
    assert names[0] == "state" and names[-1] == "done"
    assert events[0][1]["state"] == "staged" and events[0][1]["can_flip"] and events[0][1]["job"]["status"] == "completed"
    assert events[-1][1] == {"state": "staged", "verdict": "pass"}
    assert _client(cookie=None).get(f"/api/ingest/{ingest_id}/events", headers=JSON).status_code == 403
    assert c.get("/api/ingest/nope/events", headers=JSON).status_code == 404


def test_failed_ingest_with_a_build_is_flagged_as_blocking(web):
    c = _client()
    failed = _ingest(web, "failed", stage="enrich", build="B1")
    _ingest(web, "failed", stage="documents")   # no build: nothing held
    lst = c.get("/api/ingest", headers=JSON).json()
    assert lst["blocking"] == [failed]
    page = c.get("/ingest").text
    assert "may still hold the cycle" in page and "MNO-A Apr2031 is failed at enrich" in page

