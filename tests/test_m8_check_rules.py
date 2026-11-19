"""M8 校准核查保护放行 —— 纯规则测试。"""
import pytest

from app.errors import BusinessError
from app.modules.m8_check.rules import (
    PROTECTED, UNPROTECTED, UNCHECKED, RETEST_DONE,
    build_checks, evaluate, parse_sequence, plan_add_check, retest_tasks,
)

HEADER = "seq,batch,sample_id,analyte,kind,check_id,result,value"


def csv_lines(*lines):
    return HEADER + "\n" + "\n".join(lines) + "\n"


# 典型场景：前段合格核查、中间漂移、后段失保
DRIFT = csv_lines(
    "1,B09,,Pb,check,C0,PASS,0.0030",
    "2,B09,S01,Pb,sample,,,0.0031",
    "3,B09,S02,Pb,sample,,,0.0029",
    "4,B09,,Pb,check,C1,FAIL,0.0061",
    "5,B09,S03,Pb,sample,,,0.0048",
    "6,B09,S04,Pb,sample,,,0.0052",
)


def test_parse_quarantines_bad_rows_but_keeps_good():
    text = csv_lines(
        "1,B09,,Pb,check,C0,PASS,0.003",
        "x,B09,S01,Pb,sample,,,0.003",          # SEQ 非整数
        "2,B09,,Pb,check,,PASS,0.003",          # 核查缺编号
        "3,B09,,Pb,check,C2,WEIRD,0.003",       # 核查结论非法
        "4,B09,,Pb,sample2,C2,PASS,0.003",      # kind 非法
    )
    entries, bad = parse_sequence(text)
    reasons = {b["reason"] for b in bad}
    assert {"SEQ", "CHECK_ID", "CHECK_RESULT", "KIND"} <= reasons
    assert len(entries) == 1


def test_parse_header_mismatch():
    with pytest.raises(BusinessError):
        parse_sequence("a,b,c\n1,2,3\n")


def test_duplicate_check_id_quarantined():
    text = csv_lines(
        "1,B09,,Pb,check,C0,PASS,0.003",
        "2,B09,,Pb,check,C0,FAIL,0.006",
        "3,B09,S01,Pb,sample,,,0.003",
    )
    entries, _ = parse_sequence(text)
    checks, conflicts = build_checks(entries)
    assert len(checks) == 1
    assert conflicts[0]["reason"] == "CHECK_DUPLICATE"


def test_protection_split_on_drift():
    entries, _ = parse_sequence(DRIFT)
    checks, _ = build_checks(entries)
    samples, segments = evaluate(entries, checks)
    status = {s["sample_id"]: s["status"] for s in samples}
    assert status == {"S01": PROTECTED, "S02": PROTECTED,
                      "S03": UNPROTECTED, "S04": UNPROTECTED}
    # 每份样品都能对上保护它的核查
    assert {s["sample_id"]: s["protecting_check"] for s in samples} == {
        "S01": "C0", "S02": "C0", "S03": None, "S04": None}
    # 两段：受 C0 保护段 + 失保段
    assert [(seg["guard_check"], seg["protected"]) for seg in segments] == [
        ("C0", True), (None, False)]


def test_samples_before_first_check_are_unchecked():
    text = csv_lines(
        "1,B09,S01,Pb,sample,,,0.003",
        "2,B09,S02,Pb,sample,,,0.003",
        "3,B09,,Pb,check,C0,PASS,0.003",
        "4,B09,S03,Pb,sample,,,0.003",
    )
    entries, _ = parse_sequence(text)
    checks, _ = build_checks(entries)
    samples, _ = evaluate(entries, checks)
    by_id = {s["sample_id"]: s["status"] for s in samples}
    assert by_id["S01"] == UNCHECKED and by_id["S02"] == UNCHECKED
    assert by_id["S03"] == PROTECTED


def test_same_seq_check_runs_before_sample():
    text = csv_lines(
        "5,B09,,Pb,check,C2,PASS,0.003",
        "5,B09,S09,Pb,sample,,,0.003",
    )
    entries, _ = parse_sequence(text)
    checks, _ = build_checks(entries)
    samples, _ = evaluate(entries, checks)
    assert samples[0]["status"] == PROTECTED
    assert samples[0]["protecting_check"] == "C2"


def test_recovered_pass_restores_protection_after_fail():
    text = csv_lines(
        "1,B09,,Pb,check,C0,PASS,0.003",
        "2,B09,S01,Pb,sample,,,0.003",
        "3,B09,,Pb,check,C1,FAIL,0.006",
        "4,B09,S02,Pb,sample,,,0.005",
        "5,B09,,Pb,check,C2,PASS,0.003",
        "6,B09,S03,Pb,sample,,,0.003",
    )
    entries, _ = parse_sequence(text)
    checks, _ = build_checks(entries)
    samples, segments = evaluate(entries, checks)
    by_id = {s["sample_id"]: s["status"] for s in samples}
    assert by_id == {"S01": PROTECTED, "S02": UNPROTECTED, "S03": PROTECTED}
    assert [seg["guard_check"] for seg in segments] == ["C0", None, "C2"]


def test_retest_tasks_only_for_unprotected_and_dedup():
    entries, _ = parse_sequence(DRIFT)
    checks, _ = build_checks(entries)
    samples, _ = evaluate(entries, checks)
    tasks = retest_tasks(samples)
    assert {t["sample_id"] for t in tasks} == {"S03", "S04"}
    # 已存在身份不新增
    existing = {("B09", "S03", "Pb")}
    again = retest_tasks(samples, existing)
    assert {t["sample_id"] for t in again} == {"S04"}


def test_covered_keys_marked_retest_done():
    # 复测合格核查 C2 落在漂移样品同位（先核后样），样品才具备被覆盖前提
    text = csv_lines(
        "1,B09,,Pb,check,C0,PASS,0.003",
        "2,B09,S01,Pb,sample,,,0.003",
        "3,B09,,Pb,check,C1,FAIL,0.006",
        "4,B09,S02,Pb,sample,,,0.005",
        "5,B09,,Pb,check,C2,PASS,0.003",
        "5,B09,S03,Pb,sample,,,0.003",
        "6,B09,S04,Pb,sample,,,0.003",
    )
    entries, _ = parse_sequence(text)
    checks, _ = build_checks(entries)
    covered = {("B09", "S03", "Pb"), ("B09", "S04", "Pb")}
    samples, _ = evaluate(entries, checks, covered)
    by_id = {s["sample_id"]: s["status"] for s in samples}
    assert by_id["S03"] == RETEST_DONE and by_id["S04"] == RETEST_DONE
    # 未在覆盖集合中的同样受保护样品仍为普通 PROTECTED
    assert by_id["S01"] == PROTECTED


def test_plan_add_check_rejects_duplicate_and_bad_result():
    checks = [{"check_id": "C0", "seq": 1, "result": "PASS"}]
    with pytest.raises(BusinessError):
        plan_add_check({"check_id": "C0", "result": "PASS"}, checks)
    with pytest.raises(BusinessError):
        plan_add_check({"check_id": "C9", "result": "X"}, checks)


def test_no_check_means_all_unchecked_one_segment():
    text = csv_lines("1,B09,S01,Pb,sample,,,0.003", "2,B09,S02,Pb,sample,,,0.003")
    entries, _ = parse_sequence(text)
    checks, _ = build_checks(entries)
    samples, segments = evaluate(entries, checks)
    assert all(s["status"] == UNCHECKED for s in samples)
    assert len(segments) == 1 and segments[0]["guard_check"] is None


def test_check_only_batch_has_no_samples_or_tasks():
    text = csv_lines("1,B09,,Pb,check,C0,PASS,0.003")
    entries, _ = parse_sequence(text)
    checks, _ = build_checks(entries)
    samples, segments = evaluate(entries, checks)
    assert samples == [] and segments == []
    assert retest_tasks(samples) == []


@pytest.mark.parametrize("bad_text", ["", "   \n"])
def test_empty_input_rejected(bad_text):
    with pytest.raises(BusinessError):
        parse_sequence(bad_text)


def test_header_only_rejected():
    with pytest.raises(BusinessError):
        parse_sequence(HEADER + "\n")
