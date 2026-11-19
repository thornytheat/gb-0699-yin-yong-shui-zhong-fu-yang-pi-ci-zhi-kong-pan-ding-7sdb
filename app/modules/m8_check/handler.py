"""M8 核查保护放行 HTTP 路由。"""
from fastapi import APIRouter, Body
from fastapi.responses import Response

from app.errors import BusinessError, object_payload
from app.modules.m8_check import service
from app.modules.m8_check.exporter import evidence_csv

router = APIRouter(prefix="/api/check")


@router.post("/batches")
def create_batch(payload: object = Body(default=None)):
    payload = object_payload(payload)
    text = payload.get("text")
    if not isinstance(text, str):
        raise BusinessError("TEXT", "输入须为仪器序列CSV文本")
    return service.ingest(text)

@router.get("/batches")
def list_batches():
    return {"batches": service.list_all()}


@router.get("/batches/{ident}")
def get_batch(ident: str):
    return service.get_view(ident)


@router.get("/batches/{ident}/export")
def export_batch(ident: str):
    view = service.get_view(ident)
    return Response(evidence_csv(view), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=check-evidence.csv"})


@router.post("/batches/{ident}/checks")
def add_check(ident: str, payload: object = Body(...)):
    payload = object_payload(payload)
    check = {
        "check_id": payload.get("check_id"),
        "seq": payload.get("seq"),
        "result": payload.get("result"),
        "value": payload.get("value"),
    }
    if not isinstance(check["check_id"], str) or not check["check_id"].strip():
        raise BusinessError("CHECK_ID", "核查编号缺失")
    if check["seq"] is not None and type(check["seq"]) is not int:
        raise BusinessError("SEQ", "序列号须为整数")
    return service.add_check(ident, check)
