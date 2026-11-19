"""M8 证据 CSV：核查台账 + 逐样放行（绑定保护核查）+ 复测任务。"""
import csv
import io
import json


def evidence_csv(view):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["batch_id", "revision", "section", "key", "payload"])
    for c in view["checks"]:  # 台账完整导出，证明无记录丢失
        w.writerow([view["id"], view["revision"], "check", c["check_id"],
                    json.dumps(c, ensure_ascii=False)])
    for r in view["releases"]:
        w.writerow([view["id"], view["revision"], "release", r["sample_id"],
                    json.dumps(r, ensure_ascii=False)])
    for t in view["tasks"]:
        w.writerow([view["id"], view["revision"], "retest", t["sample_id"],
                    json.dumps(t, ensure_ascii=False)])
    for s in view["samples"]:
        w.writerow([view["id"], view["revision"], "sample", s["sample_id"],
                    json.dumps({k: s[k] for k in
                                ("sample_id", "status", "protecting_check",
                                 "released", "waiting_evidence", "seq")}, ensure_ascii=False)])
    return "﻿" + out.getvalue()
