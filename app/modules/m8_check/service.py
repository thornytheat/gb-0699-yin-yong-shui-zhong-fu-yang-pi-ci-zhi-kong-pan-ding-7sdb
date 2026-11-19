"""M8 核查保护放行 —— 编排服务。

不变量：
1. 放行决策只追加：每份放行结果永久绑定其保护核查，任何重判不回收旧结论
   （更新未完成时保留原放行结论）。
2. 复测任务按身份 (batch,sample_id,analyte) 幂等：重跑同一批不新增任务。
3. 核查台账只追加：补入合格核查后，仅对其后尚未覆盖的样品重判。
"""
import hashlib
from pathlib import Path

from app.config import RULE_VERSION
from app.modules.m8_check import rules, store


def sample_text():
    return (Path(__file__).parent.parent.parent.parent / "sample_data" / "check-sequence.csv").read_text(encoding="utf-8-sig")


def _digest(text):
    return hashlib.sha256((RULE_VERSION + "\n" + text).encode()).hexdigest()


def _batch_name(entries):
    names = {e["batch"] for e in entries}
    return sorted(names)[0] if len(names) == 1 else "MULTI"


def ingest(text):
    """接收一段仪器序列，幂等创建/返回核查批次，并落首次保护判定。"""
    entries, bad = rules.parse_sequence(text)
    checks, conflicts = rules.build_checks(entries)
    bad.extend(conflicts)
    # 冲突核查条目已隔离，必须从判定序列剔除，避免与台账（只留首条）不一致。
    conflict_ids = {c["id"] for c in conflicts}
    entries = [e for e in entries
               if not (e["kind"] == "check" and e["check_id"] in conflict_ids)]
    digest = _digest(text)
    ident = store.find_by_digest(digest)
    created = ident is None
    if created:
        ident = "CHK" + digest[:13]
        store.create_batch(ident, digest, _batch_name(entries), entries, bad)
        for c in checks:  # 初始核查全部来自上传序列
            store.append_check(ident, c, source="SEQUENCE")
    view = _rebuild(ident)
    view["created"] = created
    return view


def _rebuild(ident):
    """依据“原始序列 + 核查台账 + 已覆盖身份”重判，并幂等同步任务与放行。"""
    batch = store.get_batch(ident)
    checks = batch["checks"]
    # 台账里的核查与原始 entries 中的 check 同源；evaluate 只依赖 entries，
    # 但后补核查不在 entries 中，因此把台账核查合并进判定序列。
    ledger_entries = list(batch["entries"])
    entry_check_ids = {e["check_id"] for e in ledger_entries if e["kind"] == "check"}
    for c in checks:
        if c["check_id"] not in entry_check_ids:
            ledger_entries.append({"seq": c["seq"], "batch": batch["batch"], "sample_id": "",
                                   "analyte": "", "kind": "check", "check_id": c["check_id"],
                                   "result": c["result"], "value": c.get("value"), "line": c.get("line")})
    covered = store.covered_keys(ident)
    samples, segments = rules.evaluate(ledger_entries, checks, covered)

    # 复测任务：只为失去保护的样品创建，身份幂等（已存在不新增）。
    existing = {(t["batch_name"], t["sample_id"], t["analyte"]) for t in batch["tasks"]}
    new_tasks = rules.retest_tasks(samples, existing)
    store.insert_tasks(ident, new_tasks)

    # 放行：当前受保护且尚未放行的，追加放行；旧放行一律保留。
    already_released = store.release_keys(ident)
    fresh_releases = [r for r in rules.release_set(samples)
                      if (r["batch"], r["sample_id"], r["analyte"]) not in already_released]
    # 以数据库当前 revision 为准，避免同请求多次写入后用过期值。
    revision_after = store.current_revision(ident) + (1 if fresh_releases else 0)
    store.insert_releases(ident, fresh_releases, revision_after)

    return _view(store.get_batch(ident), samples, segments)


def add_check(ident, check):
    """补入一次核查（通常为复测后的合格核查）。台账只追加，随后仅重判其后样品。"""
    batch = store.get_batch(ident)
    normalized = rules.plan_add_check(check, batch["checks"])
    normalized.setdefault("seq", max([e["seq"] for e in batch["entries"]], default=0) + 1)
    store.append_check(ident, normalized, source="RETEST")

    closed = 0
    if normalized["result"] == "PASS":
        # 先重判，找到被这条新合格核查覆盖、其后尚未覆盖的样品，关闭对应 OPEN 任务。
        view = _rebuild(ident)
        covered_now = {(s["batch"], s["sample_id"], s["analyte"])
                       for s in view["samples"]
                       if s["protecting_check"] == normalized["check_id"]}
        closed = store.close_tasks_for(ident, covered_now, normalized["check_id"])
    final = _rebuild(ident)
    final["closed_tasks"] = closed
    return final


def _view(batch, samples, segments):
    tasks = batch["tasks"]
    releases = store.list_releases(batch["id"])
    released_index = {(r["batch_name"], r["sample_id"], r["analyte"]): r for r in releases}
    # 逐样品挂上“保护它的核查 + 放行/等待证据”状态，供页面逐样追溯。
    for s in samples:
        rel = released_index.get((s["batch"], s["sample_id"], s["analyte"]))
        s["released"] = rel is not None
        s["release_revision"] = rel["revision"] if rel else None
        s["waiting_evidence"] = s["status"] in (rules.UNPROTECTED, rules.UNCHECKED)
    status_text = {rules.PROTECTED: "受保护·已放行", rules.RETEST_DONE: "复测覆盖·已放行",
                   rules.UNPROTECTED: "漂移后·等待证据", rules.UNCHECKED: "核查前·等待证据"}
    for seg in segments:
        seg["status_text"] = ("受保护：核查 " + seg["guard_check"]) if seg["protected"] else "无合格核查保护"
    return {
        "id": batch["id"], "batch": batch["batch"], "revision": batch["revision"],
        "created": batch["created"], "updated": batch["updated"],
        "checks": batch["checks"], "quarantine": batch["quarantine"],
        "segments": [_public_segment(seg) for seg in segments],
        "samples": samples, "releases": releases, "tasks": tasks,
        "summary": rules.summarize(samples, tasks), "status_text": status_text,
    }


def _public_segment(seg):
    return {"segment": seg["segment"], "guard_check": seg["guard_check"],
            "guard_result": seg["guard_result"], "protected": seg["protected"],
            "start_seq": seg["start_seq"], "end_seq": seg["end_seq"],
            "status_text": seg["status_text"],
            "sample_ids": [s["sample_id"] for s in seg["samples"]],
            "waiting": sum(s["waiting_evidence"] for s in seg["samples"])}


def get_view(ident):
    return _rebuild(ident)


def list_all():
    return store.list_batches()
