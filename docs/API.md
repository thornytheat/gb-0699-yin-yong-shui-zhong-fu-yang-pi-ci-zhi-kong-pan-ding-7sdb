# 接口

`POST /api/run`：{"text":"CSV内容"}。同规则版本与输入摘要只产生一条批次。

`GET/POST /api/1` 至 `/api/7`：独立样例或自定义文件计算。各模块返回 report、quarantine、balance。

`POST /api/review`：id、整数revision、decision取通过或退回。旧版本409；阻断项不能通过。

`GET /api/export/{id}`：唯一证据CSV。`GET /health`：存活检查。

M8 核查保护放行（独立状态，SQLite 独立表）：
- `POST /api/check/batches`：{"text":"仪器序列CSV"}，幂等创建并返回分段保护视图。
- `GET /api/check/batches`：核查批次历史。`GET /api/check/batches/{id}`：单批视图。
- `POST /api/check/batches/{id}/checks`：补入核查 {check_id,seq?,result,value?}；编号重复 409，合格核查仅重判其后尚未覆盖样品。
- `GET /api/check/batches/{id}/export`：核查证据CSV（台账+放行绑定+复测任务）。
- 页面：`GET /module/8`。

错误：结构/数值422，版本冲突409，不存在404，文件过大413。逐行异常进入quarantine。
