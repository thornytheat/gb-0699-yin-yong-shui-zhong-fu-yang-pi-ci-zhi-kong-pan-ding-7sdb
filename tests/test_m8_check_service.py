"""M8 状态与编排不变量测试 + HTTP 契约测试。"""
import pytest

from app.errors import BusinessError
from app.modules.m8_check import service, store
from app.modules.m8_check.service import sample_text


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    # store 在导入时绑定了 DATA_DIR，需同时改 store 模块引用
    monkeypatch.setattr("app.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.modules.m8_check.store.DATA_DIR", tmp_path)
    yield


def test_ingest_idempotent_same_batch_no_new_tasks():
    first = service.ingest(sample_text())
    again = service.ingest(sample_text())
    assert first["id"] == again["id"]
    assert len(first["tasks"]) == len(again["tasks"]) == 4
    # 复测身份固定
    assert {t["sample_id"] for t in again["tasks"]} == {"S05", "S06", "S07", "S08"}


def test_front_segment_released_against_guard_check():
    view = service.ingest(sample_text())
    released = {r["sample_id"]: r["protecting_check"] for r in view["releases"]}
    assert released == {"S01": "C0", "S02": "C0", "S03": "C0", "S04": "C0"}
    # 漂移后样品不在放行里
    assert "S05" not in released


def test_check_ledger_is_append_only_no_data_loss():
    view = service.ingest(sample_text())
    original = [c["check_id"] for c in view["checks"]]
    service.add_check(view["id"], {"check_id": "C2", "seq": 7, "result": "PASS", "value": "0.0031"})
    after = service.get_view(view["id"])
    ledger_ids = [c["check_id"] for c in after["checks"]]
    # 原始两条核查一条不少，新核查追加在后
    assert original == ["C0", "C1"]
    assert ledger_ids == ["C0", "C1", "C2"]


def test_duplicate_check_rejected_and_ledger_unchanged():
    view = service.ingest(sample_text())
    before = len(store.list_checks(view["id"]))
    with pytest.raises(BusinessError) as exc:
        service.add_check(view["id"], {"check_id": "C1", "seq": 9, "result": "PASS"})
    assert exc.value.status == 409
    assert len(store.list_checks(view["id"])) == before


def test_add_pass_check_rejudges_only_after_and_keeps_old_release():
    view = service.ingest(sample_text())
    ident = view["id"]
    old_releases = service.get_view(ident)["releases"]
    old_keys = {(r["sample_id"], r["protecting_check"], r["revision"]) for r in old_releases}

    updated = service.add_check(ident, {"check_id": "C2", "seq": 7, "result": "PASS", "value": "0.0031"})

    # 原放行结论原样保留（编号+保护核查+版本都不变）
    kept = {(r["sample_id"], r["protecting_check"], r["revision"]) for r in updated["releases"][:4]}
    assert kept == old_keys
    # 只追加其后样品的新放行，且各自绑定 C2
    new = {(r["sample_id"], r["protecting_check"]) for r in updated["releases"][4:]}
    assert new == {("S05", "C2"), ("S06", "C2"), ("S07", "C2"), ("S08", "C2")}
    # 复测任务被新核查关闭
    assert all(t["status"] == "DONE" and t["closed_check"] == "C2" for t in updated["tasks"])
    assert updated["summary"]["waiting"] == 0


def test_rerun_after_completion_adds_no_retest_tasks():
    view = service.ingest(sample_text())
    service.add_check(view["id"], {"check_id": "C2", "seq": 7, "result": "PASS"})
    rerun = service.ingest(sample_text())  # 重跑同一批
    assert len(rerun["tasks"]) == 4
    assert all(t["status"] == "DONE" for t in rerun["tasks"])


def test_fail_check_added_keeps_tasks_open_and_old_release():
    view = service.ingest(sample_text())
    ident = view["id"]
    updated = service.add_check(ident, {"check_id": "C1B", "seq": 7, "result": "FAIL"})
    # 失败核查不解除失保，任务保持 OPEN，放行仍只有前段 4 份
    assert all(t["status"] == "OPEN" for t in updated["tasks"])
    assert len(updated["releases"]) == 4
    assert updated["summary"]["protected"] == 4


def test_every_sample_traces_to_guard_or_shown_waiting():
    view = service.ingest(sample_text())
    for s in view["samples"]:
        if s["released"]:
            assert s["protecting_check"] is not None
        else:
            assert s["waiting_evidence"] is True
            assert s["protecting_check"] is None


def test_duplicate_check_in_sequence_is_quarantined_not_applied():
    header = "seq,batch,sample_id,analyte,kind,check_id,result,value"
    text = "\n".join([
        header,
        "1,B09,,Pb,check,C0,PASS,0.003",
        "2,B09,S01,Pb,sample,,,0.003",
        "3,B09,,Pb,check,C0,FAIL,0.006",   # 同编号重复，应隔离
        "4,B09,S02,Pb,sample,,,0.003",
    ]) + "\n"
    view = service.ingest(text)
    assert any(q["reason"] == "CHECK_DUPLICATE" for q in view["quarantine"])
    # 台账只留首条 PASS；两份样品都受 C0 保护，重复 FAIL 不得改变判定
    assert [(c["check_id"], c["result"]) for c in view["checks"]] == [("C0", "PASS")]
    assert {s["sample_id"]: s["protecting_check"] for s in view["samples"]} == {
        "S01": "C0", "S02": "C0"}


# ---------- HTTP 契约 ----------
def test_http_flow(client):
    r = client.post("/api/check/batches", json={"text": sample_text()})
    assert r.status_code == 200
    ident = r.json()["id"]
    assert r.json()["summary"]["waiting"] == 4

    r2 = client.get(f"/api/check/batches/{ident}")
    assert r2.status_code == 200 and r2.json()["id"] == ident

    r3 = client.post(f"/api/check/batches/{ident}/checks",
                     json={"check_id": "C2", "seq": 7, "result": "PASS", "value": "0.0031"})
    assert r3.status_code == 200
    assert r3.json()["closed_tasks"] == 4

    r4 = client.post(f"/api/check/batches/{ident}/checks",
                     json={"check_id": "C2", "seq": 8, "result": "PASS"})
    assert r4.status_code == 409

    r5 = client.get("/api/check/batches")
    assert any(b["id"] == ident for b in r5.json()["batches"])


def test_http_404_for_unknown_batch(client):
    assert client.get("/api/check/batches/CHKnope").status_code == 404


def test_module8_page_renders(client):
    page = client.get("/module/8")
    assert page.status_code == 200
    assert "分段保护视图".encode() in page.content
    assert "核查台账".encode() in page.content


def test_export_keeps_full_ledger_and_release_bindings(client):
    r = client.post("/api/check/batches", json={"text": sample_text()})
    ident = r.json()["id"]
    client.post(f"/api/check/batches/{ident}/checks",
                json={"check_id": "C2", "seq": 7, "result": "PASS"})
    csv_text = client.get(f"/api/check/batches/{ident}/export").text
    # 原始 C0/C1 与补入 C2 全在，无丢失
    for cid in ("C0", "C1", "C2"):
        assert f"check,{cid}" in csv_text
    # 放行绑定保护核查
    assert "release,S01" in csv_text and '"C0"' in csv_text
    assert "release,S05" in csv_text and '"C2"' in csv_text
    assert "retest,S05" in csv_text
