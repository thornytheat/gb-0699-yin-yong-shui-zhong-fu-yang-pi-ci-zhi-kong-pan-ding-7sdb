"""M8 核查保护放行的状态存储。

与 M1-M7 共用同一 SQLite 文件，但使用独立表，互不影响。
核查台账 check_ledger 只追加、不更新、不删除，保证原始核查记录不丢失。
"""
import json
import sqlite3
from datetime import datetime, timezone

from app.config import DATA_DIR
from app.errors import BusinessError


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "runs.sqlite", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE IF NOT EXISTS check_batches ("
        "id TEXT PRIMARY KEY, digest TEXT NOT NULL, batch TEXT NOT NULL,"
        "entries TEXT NOT NULL, quarantine TEXT NOT NULL,"
        "revision INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL, updated TEXT NOT NULL)")
    db.execute(
        "CREATE TABLE IF NOT EXISTS check_ledger ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,"
        "check_id TEXT NOT NULL, seq INTEGER NOT NULL, result TEXT NOT NULL,"
        "value TEXT, line INTEGER, added_at TEXT NOT NULL, source TEXT NOT NULL,"
        "UNIQUE(batch_id, check_id))")
    db.execute(
        "CREATE TABLE IF NOT EXISTS retest_tasks ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,"
        "sample_id TEXT NOT NULL, analyte TEXT NOT NULL, batch_name TEXT NOT NULL,"
        "from_seq INTEGER NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,"
        "created_at TEXT NOT NULL, closed_at TEXT, closed_check TEXT,"
        "UNIQUE(batch_id, sample_id, analyte))")
    # 放行决策只追加：每份放行结果永久绑定保护它的那次核查，重判不回收旧结论。
    db.execute(
        "CREATE TABLE IF NOT EXISTS check_releases ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,"
        "sample_id TEXT NOT NULL, analyte TEXT NOT NULL, batch_name TEXT NOT NULL,"
        "seq INTEGER NOT NULL, value TEXT, protecting_check TEXT NOT NULL,"
        "revision INTEGER NOT NULL, released_at TEXT NOT NULL,"
        "UNIQUE(batch_id, sample_id, analyte))")
    return db


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_batch(ident, digest, batch, entries, quarantine_rows):
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO check_batches(id,digest,batch,entries,quarantine,created,updated)"
            " VALUES(?,?,?,?,?,?,?)",
            (ident, digest, batch, json.dumps(entries, ensure_ascii=False),
             json.dumps(quarantine_rows, ensure_ascii=False), _now(), _now()))
    return get_batch(ident)


def get_batch(ident):
    with connect() as db:
        row = db.execute("SELECT * FROM check_batches WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise BusinessError("NOT_FOUND", "核查批次不存在", 404)
    item = dict(row)
    item["entries"] = json.loads(item["entries"])
    item["quarantine"] = json.loads(item["quarantine"])
    item["checks"] = list_checks(ident)
    item["tasks"] = list_tasks(ident)
    return item


def find_by_digest(digest):
    with connect() as db:
        row = db.execute("SELECT id FROM check_batches WHERE digest=?", (digest,)).fetchone()
    return row["id"] if row else None


def list_batches():
    with connect() as db:
        rows = db.execute(
            "SELECT id,batch,revision,created,updated FROM check_batches ORDER BY updated DESC LIMIT 40").fetchall()
    return [dict(r) for r in rows]


def current_revision(ident):
    with connect() as db:
        row = db.execute("SELECT revision FROM check_batches WHERE id=?", (ident,)).fetchone()
    if row is None:
        raise BusinessError("NOT_FOUND", "核查批次不存在", 404)
    return row["revision"]


def list_checks(ident):
    with connect() as db:
        rows = db.execute(
            "SELECT check_id,seq,result,value,line,added_at,source FROM check_ledger"
            " WHERE batch_id=? ORDER BY seq, id", (ident,)).fetchall()
    return [dict(r) for r in rows]


def list_tasks(ident):
    with connect() as db:
        rows = db.execute(
            "SELECT sample_id,analyte,batch_name,from_seq,reason,status,created_at,closed_at,closed_check"
            " FROM retest_tasks WHERE batch_id=? ORDER BY from_seq, id", (ident,)).fetchall()
    return [dict(r) for r in rows]


def append_check(ident, check, source):
    """只追加一条核查。编号冲突由 UNIQUE 与 rules.plan_add_check 双重拦截。"""
    with connect() as db:
        try:
            db.execute(
                "INSERT INTO check_ledger(batch_id,check_id,seq,result,value,line,added_at,source)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (ident, check["check_id"], check["seq"], check["result"], check.get("value"),
                 check.get("line"), _now(), source))
        except sqlite3.IntegrityError:
            raise BusinessError("CHECK_EXISTS", "核查编号已存在，原始核查记录不可改写", 409)
        db.execute("UPDATE check_batches SET revision=revision+1, updated=? WHERE id=?", (_now(), ident))


def insert_tasks(ident, tasks):
    """幂等写入复测任务；已存在的身份不新增（重跑同一批不新增复测任务）。"""
    inserted = 0
    with connect() as db:
        for t in tasks:
            cur = db.execute(
                "INSERT OR IGNORE INTO retest_tasks"
                "(batch_id,sample_id,analyte,batch_name,from_seq,reason,status,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (ident, t["sample_id"], t["analyte"], t["batch"], t["from_seq"],
                 t["reason"], "OPEN", _now()))
            inserted += cur.rowcount
        if inserted:
            db.execute("UPDATE check_batches SET updated=? WHERE id=?", (_now(), ident))
    return inserted


def close_tasks_for(ident, covered_keys, closed_check):
    """把被新合格核查覆盖、且仍 OPEN 的复测任务关闭；返回关闭条数。"""
    closed = 0
    with connect() as db:
        for batch_name, sample_id, analyte in covered_keys:
            cur = db.execute(
                "UPDATE retest_tasks SET status='DONE', closed_at=?, closed_check=?"
                " WHERE batch_id=? AND sample_id=? AND analyte=? AND status='OPEN'",
                (_now(), closed_check, ident, sample_id, analyte))
            closed += cur.rowcount
        if closed:
            db.execute("UPDATE check_batches SET revision=revision+1, updated=? WHERE id=?", (_now(), ident))
    return closed


def covered_keys(ident):
    """已完成复测并被合格核查覆盖的样品身份（status=DONE）。"""
    with connect() as db:
        rows = db.execute(
            "SELECT batch_name,sample_id,analyte FROM retest_tasks"
            " WHERE batch_id=? AND status='DONE'", (ident,)).fetchall()
    return {(r["batch_name"], r["sample_id"], r["analyte"]) for r in rows}


def open_task_keys(ident):
    with connect() as db:
        rows = db.execute(
            "SELECT batch_name,sample_id,analyte FROM retest_tasks"
            " WHERE batch_id=? AND status='OPEN'", (ident,)).fetchall()
    return {(r["batch_name"], r["sample_id"], r["analyte"]) for r in rows}


def list_releases(ident):
    with connect() as db:
        rows = db.execute(
            "SELECT sample_id,analyte,batch_name,seq,value,protecting_check,revision,released_at"
            " FROM check_releases WHERE batch_id=? ORDER BY seq, id", (ident,)).fetchall()
    return [dict(r) for r in rows]


def release_keys(ident):
    with connect() as db:
        rows = db.execute(
            "SELECT batch_name,sample_id,analyte FROM check_releases WHERE batch_id=?",
            (ident,)).fetchall()
    return {(r["batch_name"], r["sample_id"], r["analyte"]) for r in rows}


def insert_releases(ident, releases, revision):
    """幂等追加放行；已放行身份不重复写入（原放行结论保留）。返回新增条数。"""
    inserted = 0
    with connect() as db:
        for r in releases:
            cur = db.execute(
                "INSERT OR IGNORE INTO check_releases"
                "(batch_id,sample_id,analyte,batch_name,seq,value,protecting_check,revision,released_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (ident, r["sample_id"], r["analyte"], r["batch"], r["seq"],
                 r.get("value"), r["protecting_check"], revision, _now()))
            inserted += cur.rowcount
        if inserted:
            db.execute("UPDATE check_batches SET updated=? WHERE id=?", (_now(), ident))
    return inserted
