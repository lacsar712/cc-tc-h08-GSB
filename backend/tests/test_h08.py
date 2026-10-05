"""H08 验收：拒收必须真拒收，成功必须真入库。

- 只读账号报送 → 403，只说明原因，不给假成功、不塞空行、库不加行
- 未登录报送 → 401
- 测量员报送 → 201 且响应行与库中真实行一致（id>0、pending）
- 非法报文 → 400，库不加行
- 页面填报口只对测量员开放（静态守卫 App.svelte）
"""
import os
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "h08_test.db")

import api  # noqa: E402  导入即建库播种
import claimer  # noqa: E402
from models import ConvergenceLog, SessionLocal  # noqa: E402

claimer._stop.set()  # 停掉认领线程，避免后台线程翻动测试期间的 pending 行

client = api.app.test_client()


def _login(username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.get_json()
    return res.get_json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _count():
    db = SessionLocal()
    try:
        return db.query(ConvergenceLog).count()
    finally:
        db.close()


def test_reader_rejected_reason_only_and_no_row():
    token = _login("inspector", "insp123456")
    before = _count()
    res = client.post(
        "/api/logs",
        json={"chainage": "K20+050", "delta_mm": 1.1},
        headers=_auth(token),
    )
    assert res.status_code == 403
    body = res.get_json()
    # 只说明原因
    assert "仅测量员" in body["detail"]
    # 不许假装进队成功、不许插空行
    for key in ("id", "chainage", "delta_mm", "status", "ok"):
        assert key not in body
    # 库不加行
    assert _count() == before
    rows = client.get("/api/logs", headers=_auth(token)).get_json()
    assert all(row["id"] > 0 and row["chainage"] for row in rows)


def test_unauthenticated_rejected():
    before = _count()
    res = client.post("/api/logs", json={"chainage": "K20+050", "delta_mm": 1.1})
    assert res.status_code == 401
    assert _count() == before


def test_writer_success_only_when_row_persisted():
    token = _login("surveyor", "surv123456")
    before = _count()
    res = client.post(
        "/api/logs",
        json={"chainage": "K20+050", "delta_mm": -2.4},
        headers=_auth(token),
    )
    assert res.status_code == 201
    body = res.get_json()
    # 成功提示必须对应库中真实行
    assert isinstance(body["id"], int) and body["id"] > 0
    assert body["chainage"] == "K20+050"
    assert body["status"] == "pending"
    assert _count() == before + 1
    db = SessionLocal()
    try:
        row = db.get(ConvergenceLog, body["id"])
        assert row is not None
        assert row.chainage == "K20+050"
        assert abs(row.delta_mm - (-2.4)) < 1e-9
        assert row.created_by == "surveyor"
        assert row.status == "pending"
    finally:
        db.close()
    rows = client.get("/api/logs", headers=_auth(token)).get_json()
    assert any(r["id"] == body["id"] and r["chainage"] == "K20+050" for r in rows)


def test_invalid_payload_400_no_row():
    token = _login("surveyor", "surv123456")
    before = _count()
    r1 = client.post("/api/logs", json={"chainage": "", "delta_mm": 1.0}, headers=_auth(token))
    r2 = client.post("/api/logs", json={"chainage": "K21+000", "delta_mm": "abc"}, headers=_auth(token))
    assert r1.status_code == 400 and "detail" in r1.get_json()
    assert r2.status_code == 400 and "detail" in r2.get_json()
    assert _count() == before


def test_form_closed_for_readers_in_ui():
    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.svelte").read_text(
        encoding="utf-8"
    )
    assert "h08-trap-form" not in src
    assert "isWriter = true" not in src
    assert 'session.role === "writer"' in src
