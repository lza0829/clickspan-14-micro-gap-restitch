from fastapi import APIRouter, HTTPException

from app.db import connect
from app.modules import micro_gap_restitch as mgr

router = APIRouter(prefix="/api/restitch")


@router.get("/config")
def get_config():
    conn = connect()
    try:
        mgr.ensure_schema(conn)
        return {
            "micro_gap_sec": mgr.get_micro_gap_sec(conn),
            "idle_timeout_sec": mgr.idle_timeout_sec(conn),
        }
    finally:
        conn.close()


@router.put("/config")
def put_config(body: dict):
    conn = connect()
    try:
        mgr.ensure_schema(conn)
        try:
            sec = mgr.set_micro_gap_sec(conn, body.get("micro_gap_sec"))
        except (TypeError, ValueError) as e:
            raise HTTPException(400, str(e))
        return {"micro_gap_sec": sec}
    finally:
        conn.close()


@router.get("/candidates")
def candidates():
    """扫描入口（只读）：列出当前全部误切候选对，不改库。"""
    conn = connect()
    try:
        mgr.ensure_schema(conn)
        return {"candidates": mgr.find_candidates(conn)}
    finally:
        conn.close()


@router.post("/run")
def run():
    """扫描入口（执行）：逐对事务缝回全部误切候选直至收敛。"""
    conn = connect()
    try:
        return mgr.run_scan(conn)
    finally:
        conn.close()


@router.post("/seed-fixture")
def seed_fixture():
    """装载微间隙夹具（幂等）：造出已被误切的两段。"""
    conn = connect()
    try:
        mgr.ensure_schema(conn)
        result = mgr.seed_fixture(conn)
        conn.commit()
        return result
    finally:
        conn.close()


@router.get("/edges")
def edges():
    """缝回边审计（只读）：被吞、保留、微间隙秒。"""
    conn = connect()
    try:
        return mgr.list_edges(conn)
    finally:
        conn.close()


@router.get("/verify")
def verify():
    """缝回核证（只读）：逐边核对 + 残留扫描。"""
    conn = connect()
    try:
        return mgr.verify(conn)
    finally:
        conn.close()
