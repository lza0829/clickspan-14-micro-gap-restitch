"""micro_gap_restitch 验收：夹具造微间隙两段，缝回后一段串珠。

运行：cd backend && python -m pytest tests/test_micro_gap_restitch.py -q
"""
import os
import sys
import tempfile
from pathlib import Path

# 必须在导入 app 之前固定数据目录（db.py 在导入时定型）
_TMP = tempfile.mkdtemp(prefix="clickspan-restitch-test-")
os.environ["DATA_DIR"] = _TMP
os.environ["FIXTURES_DIR"] = str(Path(__file__).resolve().parents[2] / "fixtures")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import micro_gap_restitch as mgr  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # 触发 startup → init_db → 夹具落库
        yield c


def test_fixture_seeds_two_mis_cut_segments(client):
    r = client.get("/api/sessions", params={"visitor_id": "V-2001"})
    assert r.status_code == 200
    rows = r.json()
    assert [s["id"] for s in rows] == ["S-V-2001-a", "S-V-2001-b"]
    assert rows[0]["status"] == "closed" and rows[1]["status"] == "open"
    assert rows[0]["ended_at"] == "2026-11-18T08:04:00Z"
    assert rows[1]["started_at"] == "2026-11-18T08:04:45Z"  # 间隙 45s


def test_candidates_scan_is_read_only(client):
    r = client.get("/api/restitch/candidates")
    assert r.status_code == 200
    cands = r.json()["candidates"]
    assert len(cands) == 1
    c = cands[0]
    assert c["prev_session_id"] == "S-V-2001-a"
    assert c["next_session_id"] == "S-V-2001-b"
    assert c["gap_sec"] == 45
    # V-2002 间隙 630s > 120s，不是候选；扫描只读，会话数不变
    assert len(client.get("/api/sessions").json()) == 6


def test_config_validation(client):
    assert client.put("/api/restitch/config", json={"micro_gap_sec": 1800}).status_code == 400
    assert client.put("/api/restitch/config", json={"micro_gap_sec": 0}).status_code == 400
    r = client.put("/api/restitch/config", json={"micro_gap_sec": 90})
    assert r.status_code == 200 and r.json()["micro_gap_sec"] == 90
    # 空闲超时不得降到微间隙及以下
    r = client.put("/api/settings/idle-timeout", json={"idle_timeout_sec": 60})
    assert r.status_code == 400
    # 还原
    assert client.put("/api/restitch/config", json={"micro_gap_sec": 120}).status_code == 200


def test_run_restitch_yields_one_strand(client):
    r = client.post("/api/restitch/run")
    assert r.status_code == 200
    report = r.json()
    assert report["ok"] and len(report["restitched"]) == 1
    st = report["restitched"][0]
    assert st["retained_session_id"] == "S-V-2001-a"
    assert st["swallowed_session_id"] == "S-V-2001-b"
    assert st["moved_hit_count"] == 2 and st["hit_count"] == 4

    # 对外只剩一个会话 id
    rows = client.get("/api/sessions", params={"visitor_id": "V-2001"}).json()
    assert [s["id"] for s in rows] == ["S-V-2001-a"]
    # 一段串珠：MG1→MG4 顺序成串
    detail = client.get("/api/sessions/S-V-2001-a").json()
    assert [h["id"] for h in detail["hits"]] == ["MG1", "MG2", "MG3", "MG4"]
    sess = detail["session"]
    assert sess["started_at"] == "2026-11-18T08:00:00Z"
    assert sess["ended_at"] == "2026-11-18T08:06:00Z"
    assert sess["hit_count"] == 4 and sess["status"] == "open"
    # 被吞会话对外查不到
    r = client.get("/api/sessions/S-V-2001-b")
    assert r.status_code == 404 and "缝回并入 S-V-2001-a" in r.json()["detail"]
    # 总览口径
    ov = client.get("/api/overview").json()
    assert ov["sessions"] == 5 and ov["sessions_merged_away"] == 1
    assert ov["restitch_edges"] == 1


def test_edge_persisted(client):
    edges = client.get("/api/restitch/edges").json()
    assert len(edges) == 1
    e = edges[0]
    assert e["swallowed_session_id"] == "S-V-2001-b"
    assert e["retained_session_id"] == "S-V-2001-a"
    assert e["gap_sec"] == 45 and e["micro_gap_sec"] == 120
    assert e["moved_hit_count"] == 2
    assert e["prev_ended_at"] == "2026-11-18T08:04:00Z"
    assert e["next_started_at"] == "2026-11-18T08:04:45Z"


def test_verify_ok(client):
    r = client.get("/api/restitch/verify")
    assert r.status_code == 200
    v = r.json()
    assert v["ok"] and v["edges_checked"] == 1 and v["residual_candidates"] == 0
    assert all(c["ok"] for c in v["checks"])


def test_rescan_is_idempotent(client):
    report = client.post("/api/restitch/run").json()
    assert report["ok"] and report["restitched"] == [] and report["remaining_candidates"] == 0
    assert len(client.get("/api/restitch/edges").json()) == 1


def test_unrelated_sessions_untouched(client):
    rows = client.get("/api/sessions", params={"visitor_id": "V-2002"}).json()
    assert [s["id"] for s in rows] == ["S-V-2002-a", "S-V-2002-b"]
    assert all(s["hit_count"] == 2 for s in rows)
    d = client.get("/api/sessions/S-V-1001-1").json()
    assert [h["id"] for h in d["hits"]] == ["H1", "H2", "H3"]
    assert len(client.get("/api/visitors").json()) == 4


def test_chain_restitch_converges(client):
    """链式微间隙 A-B-C：扫描循环逐对缝回，最终一段串珠、两条边。"""
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO visitors(id, label, created_at) VALUES ('V-8','访客八','2026-11-18T00:00:00Z')"
        )
        conn.execute("INSERT INTO sessions VALUES ('C1','V-8','2026-11-18T02:00:00Z','2026-11-18T02:01:00Z',1,'closed')")
        conn.execute("INSERT INTO sessions VALUES ('C2','V-8','2026-11-18T02:01:30Z','2026-11-18T02:02:00Z',1,'closed')")
        conn.execute("INSERT INTO sessions VALUES ('C3','V-8','2026-11-18T02:02:20Z','2026-11-18T02:03:00Z',1,'open')")
        conn.execute("INSERT INTO hits VALUES ('HC1','V-8','C1','2026-11-18T02:00:00Z','/a','view','primary')")
        conn.execute("INSERT INTO hits VALUES ('HC2','V-8','C2','2026-11-18T02:01:30Z','/b','view','primary')")
        conn.execute("INSERT INTO hits VALUES ('HC3','V-8','C3','2026-11-18T02:02:20Z','/c','view','primary')")
        conn.commit()
        report = mgr.run_scan(conn)
        assert report["ok"] and len(report["restitched"]) == 2
        row = conn.execute(
            "SELECT started_at, ended_at, hit_count, status FROM sessions WHERE id='C1'"
        ).fetchone()
        assert (row["started_at"], row["ended_at"], row["hit_count"], row["status"]) == (
            "2026-11-18T02:00:00Z", "2026-11-18T02:02:20Z", 3, "open",
        )
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM hits WHERE session_id='C1' ORDER BY event_ts"
        ).fetchall()]
        assert ids == ["HC1", "HC2", "HC3"]
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM restitch_edges WHERE visitor_id='V-8'"
        ).fetchone()["c"] == 2
    finally:
        conn.execute("DELETE FROM hits WHERE visitor_id='V-8'")
        conn.execute("DELETE FROM restitch_edges WHERE visitor_id='V-8'")
        conn.execute("DELETE FROM session_proofs WHERE visitor_id='V-8'")
        conn.execute("DELETE FROM sessions WHERE visitor_id='V-8'")
        conn.execute("DELETE FROM visitors WHERE id='V-8'")
        conn.commit()
        conn.close()


def test_failure_closure_rolls_back_mid_tx(client):
    """写边失败时整体回滚：成员未改挂、被吞未关闭、与本对无关者不动。"""
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO visitors(id, label, created_at) VALUES ('V-9','访客九','2026-11-18T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('X1','V-9','2026-11-18T01:00:00Z','2026-11-18T01:01:00Z',1,'closed')"
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('X2','V-9','2026-11-18T01:01:30Z','2026-11-18T01:02:00Z',1,'open')"
        )
        conn.execute(
            "INSERT INTO hits VALUES ('HX1','V-9','X1','2026-11-18T01:00:00Z','/a','view','primary')"
        )
        conn.execute(
            "INSERT INTO hits VALUES ('HX2','V-9','X2','2026-11-18T01:01:30Z','/b','view','primary')"
        )
        # 预置冲突边：缝回写边时触发 UNIQUE(swallowed_session_id)，制造事务中途失败
        conn.execute(
            "INSERT INTO sessions VALUES ('X0','V-9','2026-11-18T00:00:00Z','2026-11-18T00:00:10Z',0,'closed')"
        )
        conn.execute(
            """
            INSERT INTO restitch_edges(
              visitor_id, swallowed_session_id, retained_session_id,
              gap_sec, micro_gap_sec, prev_ended_at, next_started_at, moved_hit_count, created_at
            ) VALUES ('V-9','X2','X0',5,120,'2026-11-18T00:00:10Z','2026-11-18T00:00:15Z',0,'2026-11-18T00:00:20Z')
            """
        )
        conn.commit()

        with pytest.raises(Exception):
            mgr.restitch_pair(conn, "X1", "X2")

        # 失败闭：一切如未发生
        assert conn.execute("SELECT session_id FROM hits WHERE id='HX2'").fetchone()["session_id"] == "X2"
        assert conn.execute("SELECT status, hit_count FROM sessions WHERE id='X2'").fetchone()["status"] == "open"
        row = conn.execute("SELECT hit_count, ended_at FROM sessions WHERE id='X1'").fetchone()
        assert row["hit_count"] == 1 and row["ended_at"] == "2026-11-18T01:01:00Z"
        assert conn.execute("SELECT COUNT(*) AS c FROM restitch_edges WHERE retained_session_id='X1'").fetchone()["c"] == 0

        # 校验失败同样不留痕：间隙超限的对拒绝缝回
        with pytest.raises(ValueError):
            mgr.restitch_pair(conn, "S-V-2002-a", "S-V-2002-b")
        assert conn.execute("SELECT status FROM sessions WHERE id='S-V-2002-b'").fetchone()["status"] == "open"
    finally:
        conn.execute("DELETE FROM hits WHERE visitor_id='V-9'")
        conn.execute("DELETE FROM restitch_edges WHERE visitor_id='V-9'")
        conn.execute("DELETE FROM sessions WHERE visitor_id='V-9'")
        conn.execute("DELETE FROM visitors WHERE id='V-9'")
        conn.commit()
        conn.close()
