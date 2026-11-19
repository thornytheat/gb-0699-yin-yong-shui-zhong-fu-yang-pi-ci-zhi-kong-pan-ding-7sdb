"""M8 页面上下文：让每份样品对上保护它的核查，未决样品显式等待证据。"""
from app.modules.m8_check import service


def build_context(request, legacy_run, modules):
    # M8 使用独立的核查批次状态；进入页面即幂等载入演示序列。
    view = service.ingest(service.sample_text())
    return {"title": "校准核查保护放行", "modules": modules, "active": 8,
            "run": legacy_run, "check_view": view}
