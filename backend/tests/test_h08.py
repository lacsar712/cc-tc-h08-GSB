"""拒收口径：被挡住只说明原因、库不加行、不插空行；只有测量员真正写入库才有 201 和新行。"""
import os
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="h08_test_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMPDIR, "test.db")

import api  # noqa: E402
from models import ConvergenceLog, SessionLocal  # noqa: E402


def _client():
    api.app.config["TESTING"] = True
    return api.app.test_client()


def _login(client, username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return {"Authorization": "Bearer " + res.get_json()["access_token"]}


def _db_count():
    db = SessionLocal()
    try:
        return db.query(ConvergenceLog).count()
    finally:
        db.close()


def test_reader_rejected_reason_only_no_row_no_fake_success():
    client = _client()
    headers = _login(client, "inspector", "insp123456")
    before = _db_count()

    res = client.post("/api/logs", json={"chainage": "K20+050", "delta_mm": 1.1}, headers=headers)

    # 只说明原因，不得假装进队
    assert res.status_code == 403
    data = res.get_json()
    assert "仅测量员" in data["detail"]
    assert "id" not in data and "ok" not in data and "status" not in data

    # 库不加行，列表里也不许多出空行
    assert _db_count() == before
    rows = client.get("/api/logs", headers=headers).get_json()
    assert len(rows) == before
    assert all(r["id"] and r["chainage"] for r in rows)


def test_writer_submit_actually_persists():
    client = _client()
    headers = _login(client, "surveyor", "surv123456")
    before = _db_count()

    res = client.post("/api/logs", json={"chainage": "K20+050", "delta_mm": 2.5}, headers=headers)

    # 真正写入库才有 201 和新行
    assert res.status_code == 201
    data = res.get_json()
    assert data["id"] and data["id"] > 0
    assert data["chainage"] == "K20+050"
    assert _db_count() == before + 1
    db = SessionLocal()
    try:
        row = db.get(ConvergenceLog, data["id"])
        assert row is not None
        assert row.created_by == "surveyor"
        assert row.chainage == "K20+050"
    finally:
        db.close()


def test_invalid_payload_rejected_without_row():
    client = _client()
    headers = _login(client, "surveyor", "surv123456")
    before = _db_count()

    res = client.post("/api/logs", json={"chainage": "", "delta_mm": 1.0}, headers=headers)
    assert res.status_code == 400
    assert res.get_json()["detail"]

    res = client.post("/api/logs", json={"chainage": "K21+000", "delta_mm": "abc"}, headers=headers)
    assert res.status_code == 400
    assert res.get_json()["detail"]

    assert _db_count() == before


def test_anonymous_rejected():
    client = _client()
    before = _db_count()
    res = client.post("/api/logs", json={"chainage": "K20+050", "delta_mm": 1.0})
    assert res.status_code == 401
    assert _db_count() == before
