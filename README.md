# 工伤事故调查与纠正措施

记录工伤经过、伤害、现场和证人，维护调查、纠正措施、验证与关闭流程。同工位同原因事故自动串成复发链：新事故带出近180天已结案的同工位同原因事故、上次纠正措施摘要和复发次数，并将风险级别提高一档；工位编号或伤害原因未填时事故留在“待关联”，补齐后才能进入调查。

## 模块结构

- `app.py`：参数解析、依赖组装和HTTP服务启动。
- `src/domain.py`：数据结构、错误、状态和基础校验。
- `src/rules.py`：状态机、角色矩阵、优先级、期限和关闭不变量。
- `src/repository.py`：SQLite建表、事务、版本控制和审计链。
- `src/service.py`：权限检查、用例编排、并发控制和审计。
- `src/http_api.py`：JSON路由和统一错误响应。
- `src/audit.py`：UTC时间和SHA-256审计事件。
- `static/index.html`：最小演示页。
- `tests/`：完整流程、规则和失败测试。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8311
```

默认端口为`8311`，首次启动自动建库。使用`X-Actor`和`X-Role`请求头传递身份。

## 主要接口

- `GET /health`
- `GET /api/items`
- `POST /api/items`，可提交`workstation`（工位编号）和`injury_cause`（伤害原因）
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `POST /api/items/{id}/link`，待关联事故补齐工位编号和伤害原因
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/audit`

允许角色：reporter, investigator, safety_manager, viewer。严重度越高、伤害指数越大或未关闭措施越多，优先级越高；严重事故必须在4小时内启动调查。

## 复发链

- 新事故记录`workstation`和`injury_cause`后，自动查找近180天已结案的同工位同原因事故。
- 列表和详情的`recurrence`字段给出复发依据（`basis`）、复发次数（`count`）、关联事故（`linked_items`）和上次纠正措施摘要（`last_corrective_summary`）。
- 有复发时`risk_level`比申报`severity`提高一档（fatal封顶），优先级、期限和升级判断按`risk_level`计算；原事故结论和记录不被修改。
- 工位或原因缺失的事故处于`pending_link`（待关联），通过`POST /api/items/{id}/link`补齐后转为`reported`，之后才能进入调查。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
