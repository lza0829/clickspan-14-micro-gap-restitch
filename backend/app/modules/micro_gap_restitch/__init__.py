"""亚超时误切强制缝回。

切窗偶发把间隙小于 micro_gap_sec 的两段误切成两会话，统计偏碎。本模块扫描
同访客相邻会话对，若 next.started_at - prev.ended_at ≤ micro_gap_sec 则缝回
为单一对外会话：被吞会话标 merged_away，成员改挂保留会话，只留一个会话 id
对外可查。

落库形态（择一已选）：缝回边表 restitch_edges（被吞、保留、微间隙秒、边界
时间戳、迁移点数），可审计。
失败闭（择一已选）：同事务改挂成员 / 关闭被吞 / 写边 / 写证明，任一步失败
整体回滚，不留半缝状态。

本题是误切缝合：只对相邻且间隙 ≤ micro_gap_sec 的对动手；不是访客永恒单轴，
也不是跨会话全局重排。
"""
import json
from datetime import datetime, timezone

from app.db import FIXTURES_DIR

STATUS = "implemented"

DEFAULT_MICRO_GAP_SEC = 120
FIXTURE_NAME = "micro_gap_restitch.json"

EDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS restitch_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  visitor_id TEXT NOT NULL,
  swallowed_session_id TEXT NOT NULL REFERENCES sessions(id),
  retained_session_id TEXT NOT NULL REFERENCES sessions(id),
  gap_sec REAL NOT NULL,
  micro_gap_sec REAL NOT NULL,
  prev_ended_at TEXT NOT NULL,
  next_started_at TEXT NOT NULL,
  moved_hit_count INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(swallowed_session_id)
);
"""


# ---------------------------------------------------------------- 配置

def ensure_schema(conn):
    conn.executescript(EDGE_SCHEMA)
    row = conn.execute("SELECT value FROM settings WHERE key='micro_gap_sec'").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('micro_gap_sec', ?)",
            (str(DEFAULT_MICRO_GAP_SEC),),
        )


def idle_timeout_sec(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='idle_timeout_sec'").fetchone()
    return int(row["value"]) if row else 1800


def get_micro_gap_sec(conn) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key='micro_gap_sec'").fetchone()
    return int(row["value"]) if row else DEFAULT_MICRO_GAP_SEC


def set_micro_gap_sec(conn, sec) -> int:
    """微间隙必须为正且严格小于 idle_timeout_sec，否则拒绝写入。"""
    sec = int(sec)
    idle = idle_timeout_sec(conn)
    if sec <= 0:
        raise ValueError("微间隙须为正整数秒")
    if sec >= idle:
        raise ValueError(f"微间隙须严格小于空闲超时（当前 {idle} 秒）")
    conn.execute(
        "INSERT OR REPLACE INTO settings(key, value) VALUES ('micro_gap_sec', ?)",
        (str(sec),),
    )
    conn.commit()
    return sec


# ---------------------------------------------------------------- 夹具

def seed_fixture(conn) -> dict:
    """夹具造微间隙两段：把夹具段落落成「已被误切」的相邻会话（幂等）。"""
    path = FIXTURES_DIR / FIXTURE_NAME
    if not path.exists():
        return {"seeded": False, "reason": f"缺少 {FIXTURE_NAME}"}
    data = json.loads(path.read_text(encoding="utf-8"))
    seg_defs = data.get("segments", [])
    if not seg_defs:
        return {"seeded": False, "reason": "夹具无段落"}
    first = seg_defs[0]["session_id"]
    if conn.execute("SELECT 1 FROM sessions WHERE id=?", (first,)).fetchone():
        return {"seeded": False, "reason": "夹具已装载"}
    for v in data.get("visitors", []):
        conn.execute(
            "INSERT OR IGNORE INTO visitors(id, label, created_at) VALUES (?,?,?)",
            (v["id"], v["label"], v["created_at"]),
        )
    for seg in seg_defs:
        hits = sorted(seg["hits"], key=lambda h: (h["event_ts"], h["id"]))
        conn.execute(
            """
            INSERT INTO sessions(id, visitor_id, started_at, ended_at, hit_count, status)
            VALUES (?,?,?,?,?,?)
            """,
            (
                seg["session_id"],
                seg["visitor_id"],
                hits[0]["event_ts"],
                hits[-1]["event_ts"],
                len(hits),
                seg.get("status", "closed"),
            ),
        )
        ids = []
        for h in hits:
            conn.execute(
                """
                INSERT INTO hits(id, visitor_id, session_id, event_ts, page_path, event_name, channel)
                VALUES (?,?,?,?,?,?,'primary')
                """,
                (
                    h["id"],
                    seg["visitor_id"],
                    seg["session_id"],
                    h["event_ts"],
                    h["page_path"],
                    h["event_name"],
                ),
            )
            ids.append(h["id"])
        conn.execute(
            """
            INSERT INTO session_proofs(session_id, visitor_id, hit_ids_json, hit_count, started_at, ended_at, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                seg["session_id"],
                seg["visitor_id"],
                json.dumps(ids, ensure_ascii=False),
                len(ids),
                hits[0]["event_ts"],
                hits[-1]["event_ts"],
                _now(),
            ),
        )
    return {"seeded": True, "segments": len(seg_defs)}


# ---------------------------------------------------------------- 扫描

def find_candidates(conn) -> list:
    """扫描同访客相邻会话对：间隙落在 [0, micro_gap_sec] 即误切候选。

    负间隙（重叠）属另一类异常，不在本模块范围；只读，不改库。
    """
    micro = get_micro_gap_sec(conn)
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT id, visitor_id, started_at, ended_at, hit_count, status
            FROM sessions
            WHERE status != 'merged_away'
            ORDER BY visitor_id, started_at, id
            """
        ).fetchall()
    ]
    out = []
    for prev, nxt in zip(rows, rows[1:]):
        if prev["visitor_id"] != nxt["visitor_id"]:
            continue
        if not prev["ended_at"] or not nxt["started_at"]:
            continue
        gap = (_parse_ts(nxt["started_at"]) - _parse_ts(prev["ended_at"])).total_seconds()
        if 0 <= gap <= micro:
            out.append(
                {
                    "visitor_id": prev["visitor_id"],
                    "prev_session_id": prev["id"],
                    "next_session_id": nxt["id"],
                    "prev_ended_at": prev["ended_at"],
                    "next_started_at": nxt["started_at"],
                    "gap_sec": gap,
                    "micro_gap_sec": micro,
                }
            )
    return out


def run_scan(conn) -> dict:
    """扫描入口：循环缝回全部误切对直至收敛（链式微间隙逐对缝）。

    每对各自一个事务；某对失败即整体回滚该对并终止扫描、上报错误，
    不在失败对上空转。
    """
    ensure_schema(conn)
    restitched, errors = [], []
    while True:
        candidates = find_candidates(conn)
        if not candidates:
            break
        c = candidates[0]
        try:
            restitched.append(restitch_pair(conn, c["prev_session_id"], c["next_session_id"]))
        except Exception as e:
            errors.append(
                {
                    "prev_session_id": c["prev_session_id"],
                    "next_session_id": c["next_session_id"],
                    "error": str(e),
                }
            )
            break
    return {
        "ok": not errors,
        "restitched": restitched,
        "errors": errors,
        "remaining_candidates": len(find_candidates(conn)),
    }


# ---------------------------------------------------------------- 缝回

def restitch_pair(conn, prev_session_id: str, next_session_id: str) -> dict:
    """缝回一对误切会话：保留前段、吞掉后段。

    同事务完成：候选校验（同访客 / 相邻 / 间隙 ≤ micro_gap_sec）→ 改挂成员
    → 重算保留会话边界 → 关闭被吞（merged_away）→ 写缝回边 → 写新证明。
    任一步失败整体回滚，对外绝不出现两半截状态。
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        micro = get_micro_gap_sec(conn)
        prev = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (prev_session_id,)
        ).fetchone()
        nxt = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (next_session_id,)
        ).fetchone()
        if not prev or not nxt:
            raise ValueError("候选会话不存在")
        if prev["status"] == "merged_away" or nxt["status"] == "merged_away":
            raise ValueError("候选会话已被缝回，不可重复缝")
        if prev["visitor_id"] != nxt["visitor_id"]:
            raise ValueError("仅同访客相邻会话可缝回")
        between = conn.execute(
            """
            SELECT COUNT(*) AS c FROM sessions
            WHERE visitor_id=? AND status != 'merged_away'
              AND started_at > ? AND started_at < ?
            """,
            (prev["visitor_id"], prev["started_at"], nxt["started_at"]),
        ).fetchone()["c"]
        if between:
            raise ValueError("两会话之间夹有其它会话，非相邻对")
        gap = (_parse_ts(nxt["started_at"]) - _parse_ts(prev["ended_at"])).total_seconds()
        if not 0 <= gap <= micro:
            raise ValueError(f"间隙 {gap:.0f}s 超出微间隙上限 {micro}s，拒绝缝回")

        retained, swallowed = prev_session_id, next_session_id
        # 改挂成员：只动被吞会话的击点，与本对无关的会话一行不碰
        conn.execute("UPDATE hits SET session_id=? WHERE session_id=?", (retained, swallowed))
        moved = conn.execute("SELECT changes()").fetchone()[0]
        # 重算保留会话边界，与成员击点同源
        agg = conn.execute(
            "SELECT MIN(event_ts) AS s, MAX(event_ts) AS e, COUNT(*) AS c FROM hits WHERE session_id=?",
            (retained,),
        ).fetchone()
        status = "open" if "open" in (prev["status"], nxt["status"]) else prev["status"]
        conn.execute(
            "UPDATE sessions SET started_at=?, ended_at=?, hit_count=?, status=? WHERE id=?",
            (agg["s"], agg["e"], agg["c"], status, retained),
        )
        # 关闭被吞：仅保留会话对外有效
        conn.execute(
            "UPDATE sessions SET status='merged_away', hit_count=0 WHERE id=?",
            (swallowed,),
        )
        # 写缝回边（被吞、保留、微间隙秒）
        conn.execute(
            """
            INSERT INTO restitch_edges(
              visitor_id, swallowed_session_id, retained_session_id,
              gap_sec, micro_gap_sec, prev_ended_at, next_started_at,
              moved_hit_count, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                prev["visitor_id"],
                swallowed,
                retained,
                gap,
                micro,
                prev["ended_at"],
                nxt["started_at"],
                moved,
                _now(),
            ),
        )
        # 保留会话新证明，与织机同源可核
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM hits WHERE session_id=? ORDER BY event_ts, id",
                (retained,),
            ).fetchall()
        ]
        conn.execute(
            """
            INSERT INTO session_proofs(session_id, visitor_id, hit_ids_json, hit_count, started_at, ended_at, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (retained, prev["visitor_id"], json.dumps(ids, ensure_ascii=False), len(ids), agg["s"], agg["e"], _now()),
        )
        conn.commit()
        return {
            "ok": True,
            "visitor_id": prev["visitor_id"],
            "retained_session_id": retained,
            "swallowed_session_id": swallowed,
            "gap_sec": gap,
            "moved_hit_count": moved,
            "hit_count": agg["c"],
            "started_at": agg["s"],
            "ended_at": agg["e"],
        }
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------- 核证

def list_edges(conn) -> list:
    ensure_schema(conn)
    rows = conn.execute("SELECT * FROM restitch_edges ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def merged_away_target(conn, session_id: str):
    """若 session_id 是被吞会话，返回保留会话 id；否则 None。"""
    ensure_schema(conn)
    row = conn.execute(
        "SELECT retained_session_id FROM restitch_edges WHERE swallowed_session_id=?",
        (session_id,),
    ).fetchone()
    return row["retained_session_id"] if row else None


def verify(conn) -> dict:
    """缝回核证（只读）：逐边核对被吞关闭、成员归并、边界同源，并确认无残留微间隙。"""
    ensure_schema(conn)
    checks = []
    ok = True

    def check(name, passed, detail):
        nonlocal ok
        ok = ok and bool(passed)
        checks.append({"check": name, "ok": bool(passed), "detail": detail})

    edges = list_edges(conn)
    for e in edges:
        tag = f"{e['swallowed_session_id']}→{e['retained_session_id']}"
        sw = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (e["swallowed_session_id"],)
        ).fetchone()
        rt = conn.execute(
            "SELECT * FROM sessions WHERE id=?", (e["retained_session_id"],)
        ).fetchone()
        check(
            f"被吞已关闭 {tag}",
            sw is not None and sw["status"] == "merged_away",
            f"被吞状态={sw['status'] if sw else '缺失'}",
        )
        orphan = conn.execute(
            "SELECT COUNT(*) AS c FROM hits WHERE session_id=?",
            (e["swallowed_session_id"],),
        ).fetchone()["c"]
        check(f"被吞零残留成员 {tag}", orphan == 0, f"残留击点={orphan}")
        check(
            f"保留会话对外有效 {tag}",
            rt is not None and rt["status"] != "merged_away",
            f"保留状态={rt['status'] if rt else '缺失'}",
        )
        if rt:
            agg = conn.execute(
                "SELECT MIN(event_ts) AS s, MAX(event_ts) AS e, COUNT(*) AS c FROM hits WHERE session_id=?",
                (rt["id"],),
            ).fetchone()
            check(
                f"保留会话边界与成员同源 {tag}",
                rt["started_at"] == agg["s"]
                and rt["ended_at"] == agg["e"]
                and rt["hit_count"] == agg["c"],
                f"会话=({rt['started_at']},{rt['ended_at']},{rt['hit_count']}) "
                f"击点=({agg['s']},{agg['e']},{agg['c']})",
            )
            check(
                f"保留会话覆盖缝回边界 {tag}",
                rt["started_at"] <= e["prev_ended_at"]
                and rt["ended_at"] >= e["next_started_at"],
                f"[{rt['started_at']},{rt['ended_at']}] 覆盖边 [{e['prev_ended_at']},{e['next_started_at']}]",
            )
            check(
                f"成员规模不小于迁移量 {tag}",
                agg["c"] >= e["moved_hit_count"],
                f"现成员={agg['c']} 迁移={e['moved_hit_count']}",
            )
        gap = (_parse_ts(e["next_started_at"]) - _parse_ts(e["prev_ended_at"])).total_seconds()
        check(
            f"边记录间隙自洽 {tag}",
            abs(gap - e["gap_sec"]) < 1e-6 and 0 <= e["gap_sec"] <= e["micro_gap_sec"],
            f"记录间隙={e['gap_sec']}s 上限={e['micro_gap_sec']}s",
        )

    residual = find_candidates(conn)
    check("无残留微间隙对", len(residual) == 0, f"残留候选={len(residual)}")
    leaked = conn.execute(
        "SELECT COUNT(*) AS c FROM sessions WHERE status='merged_away' AND hit_count > 0"
    ).fetchone()["c"]
    check("被吞会话一律零成员", leaked == 0, f"异常={leaked}")

    return {
        "ok": ok,
        "edges_checked": len(edges),
        "residual_candidates": len(residual),
        "checks": checks,
    }


# ---------------------------------------------------------------- 工具

def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
