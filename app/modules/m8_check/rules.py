"""M8 校准核查保护放行 —— 纯领域规则。

仪器序列上的校准核查点把整批样品切成若干段：
- 合格(PASS)核查保护其后、到下一次核查之前的样品；
- 失败(FAIL)核查不产生保护，其后样品失去保护；
- 首个核查之前的样品没有任何核查可依，一律无保护。

本模块只做纯计算，不碰数据库，便于逐条规则测试。
"""
import csv
import io
from app.errors import BusinessError

SEQUENCE_FIELDS = ["seq", "batch", "sample_id", "analyte", "kind", "check_id", "result", "value", "line"]
ENTRY_KINDS = {"check", "sample"}
CHECK_RESULTS = {"PASS", "FAIL"}

# 样品结论码
PROTECTED = "PROTECTED"        # 有合格核查保护，可放行
UNPROTECTED = "UNPROTECTED"    # 落在失败核查之后且尚未补入合格核查
UNCHECKED = "UNCHECKED"        # 首个核查之前，无任何核查
RETEST_DONE = "RETEST_DONE"    # 复测结果已被新的合格核查覆盖


def parse_sequence(text):
    """解析仪器序列 CSV，返回 (entries, quarantine_rows)。

    表头：seq,batch,sample_id,analyte,kind,check_id,result,value
    逐行异常进隔离，不中断整批解析。
    """
    if not text or not text.strip():
        raise BusinessError("EMPTY_FILE", "文件为空")
    if len(text.encode("utf-8")) > 2_000_000:
        raise BusinessError("SIZE", "文件超过2MB", 413)
    reader = csv.reader(io.StringIO(text.lstrip("﻿")), strict=True)
    try:
        header = next(reader)
    except StopIteration:
        raise BusinessError("BLANK_FILE", "文件只有空行")
    header = [h.strip() for h in header]
    expected = SEQUENCE_FIELDS[:-1]
    if set(header) != set(expected) or len(header) != len(expected):
        raise BusinessError("HEADER", "字段不匹配")
    entries, bad = [], []
    try:
        for cells in reader:
            line = reader.line_num
            if not cells or not any(c.strip() for c in cells):
                continue
            if cells == header:
                bad.append({"line": line, "id": "", "reason": "REPEATED_HEADER", "raw": cells})
                continue
            if len(cells) != len(header):
                bad.append({"line": line, "id": "", "reason": "COLUMN_COUNT", "raw": cells})
                continue
            row = dict(zip(header, [c.strip() for c in cells]), line=line)
            try:
                entries.append(_validate(row))
            except BusinessError as exc:
                bad.append({"line": line, "id": row.get("sample_id") or row.get("check_id", ""),
                            "reason": exc.code, "raw": row})
    except csv.Error:
        raise BusinessError("CSV", "引号或换行结构错误")
    if not entries and not bad:
        raise BusinessError("HEADER_ONLY", "文件只有表头")
    return entries, bad


def _validate(row):
    try:
        seq = int(row["seq"])
    except (TypeError, ValueError):
        raise BusinessError("SEQ", "序列号须为整数")
    if seq < 1:
        raise BusinessError("SEQ", "序列号须大于零")
    kind = row["kind"]
    if kind not in ENTRY_KINDS:
        raise BusinessError("KIND", "记录类别只能是 check 或 sample")
    if not row["batch"]:
        raise BusinessError("BATCH", "检测批编号缺失")
    entry = {"seq": seq, "batch": row["batch"], "sample_id": row["sample_id"],
             "analyte": row["analyte"], "kind": kind, "check_id": row["check_id"],
             "result": row["result"], "value": row["value"], "line": row["line"]}
    if kind == "check":
        if not row["check_id"]:
            raise BusinessError("CHECK_ID", "核查记录缺少核查编号")
        if row["result"] not in CHECK_RESULTS:
            raise BusinessError("CHECK_RESULT", "核查结论只能是 PASS 或 FAIL")
    else:
        if not row["sample_id"]:
            raise BusinessError("SAMPLE_ID", "样品记录缺少样品编号")
    return entry


def build_checks(entries):
    """按 seq 提取核查台账（保持顺序），同 check_id 重复登记为冲突。"""
    checks, seen, conflicts = [], set(), []
    for e in sorted((x for x in entries if x["kind"] == "check"), key=lambda x: x["seq"]):
        if e["check_id"] in seen:
            conflicts.append({"line": e["line"], "id": e["check_id"], "reason": "CHECK_DUPLICATE", "raw": e})
            continue
        seen.add(e["check_id"])
        checks.append({"check_id": e["check_id"], "seq": e["seq"], "result": e["result"],
                       "value": e["value"], "line": e["line"]})
    return checks, conflicts


def evaluate(entries, checks, covered_keys=None):
    """依据核查台账重判全部样品。

    返回 (samples, segments)。每个样品带 protecting_check（无保护为 None）与状态码。
    covered_keys: 已被复测+新合格核查覆盖的样品身份集合 (batch,sample_id,analyte)。
    重判是确定性纯函数：给定台账与覆盖集合，结论唯一，不依赖上一次结论，
    因此“更新未完成保留原结论”由调用方在持久化层处理。
    """
    covered_keys = covered_keys or set()
    # 一次性线性扫描：同 seq 时 check 先于 sample（仪器上先核后样）。
    ordered = sorted(entries, key=lambda x: (x["seq"], 0 if x["kind"] == "check" else 1))

    samples, segments = [], []
    guard = None  # 当前保护核查：最近一次 PASS；遇 FAIL 清空
    any_check_seen = False
    current = None  # 当前正在累积的段

    def guard_id(node):
        return node["check_id"] if node else None

    def flush():
        if current is not None and current["samples"]:
            current["end_seq"] = current["samples"][-1]["seq"]
            segments.append(current)

    for e in ordered:
        if e["kind"] == "check":
            any_check_seen = True
            new_guard = e if e["result"] == "PASS" else None
            if guard_id(new_guard) != guard_id(guard):
                flush()
                current = _new_segment(len(segments), new_guard)
            guard = new_guard
            continue
        if current is None or guard_id(guard) != current["guard_check"]:
            flush()
            current = _new_segment(len(segments), guard)
        if current["start_seq"] is None:
            current["start_seq"] = e["seq"]
        key = (e["batch"], e["sample_id"], e["analyte"])
        if guard is not None and guard["result"] == "PASS":
            status = RETEST_DONE if key in covered_keys else PROTECTED
            protecting = guard["check_id"]
        else:
            status = UNCHECKED if not any_check_seen else UNPROTECTED
            protecting = None
        sample = {"sample_id": e["sample_id"], "batch": e["batch"], "analyte": e["analyte"],
                  "seq": e["seq"], "value": e["value"], "line": e["line"],
                  "protecting_check": protecting, "status": status}
        samples.append(sample)
        current["samples"].append(sample)
    flush()
    return samples, segments


def _new_segment(index, guard):
    return {"segment": index, "guard_check": guard["check_id"] if guard else None,
            "guard_result": guard["result"] if guard else None,
            "protected": bool(guard and guard["result"] == "PASS"),
            "start_seq": None, "end_seq": None, "samples": []}


def retest_tasks(samples, existing_keys=None):
    """为失去保护的样品生成复测任务，身份键 (batch,sample_id,analyte) 去重。

    existing_keys 内的身份不再新增（重跑同一批不新增复测任务）。
    """
    existing_keys = existing_keys or set()
    tasks, seen = [], set()
    for s in samples:
        if s["status"] not in (UNPROTECTED, UNCHECKED):
            continue
        key = (s["batch"], s["sample_id"], s["analyte"])
        if key in seen or key in existing_keys:
            continue
        seen.add(key)
        tasks.append({"batch": s["batch"], "sample_id": s["sample_id"], "analyte": s["analyte"],
                      "from_seq": s["seq"], "reason": s["status"], "status": "OPEN"})
    return tasks


def release_set(samples):
    """放行结论：只放行有合格核查保护的样品，逐样可追溯到保护核查。"""
    released = [{"sample_id": s["sample_id"], "batch": s["batch"], "analyte": s["analyte"],
                 "value": s["value"], "protecting_check": s["protecting_check"], "seq": s["seq"]}
                for s in samples if s["status"] in (PROTECTED, RETEST_DONE)]
    return released


def summarize(samples, tasks):
    return {"total": len(samples),
            "protected": sum(s["status"] in (PROTECTED, RETEST_DONE) for s in samples),
            "waiting": sum(s["status"] in (UNPROTECTED, UNCHECKED) for s in samples),
            "retest_open": sum(t["status"] == "OPEN" for t in tasks)}


def plan_add_check(check, checks):
    """校验补入核查：编号不得与既有台账重复（台账只追加、不改写）。"""
    if check["check_id"] in {c["check_id"] for c in checks}:
        raise BusinessError("CHECK_EXISTS", "核查编号已存在，原始核查记录不可改写", 409)
    if check["result"] not in CHECK_RESULTS:
        raise BusinessError("CHECK_RESULT", "核查结论只能是 PASS 或 FAIL")
    return dict(check)
