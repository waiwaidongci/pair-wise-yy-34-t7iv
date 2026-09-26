# 工伤事故调查与纠正措施

记录工伤经过、伤害、现场和证人，维护调查、纠正措施、验证与关闭流程。

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
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `POST /api/items/{id}/link`，必须提交`expected_version`
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/audit`

允许角色：reporter, investigator, safety_manager, viewer。严重度越高、伤害指数越大或未关闭措施越多，优先级越高；严重事故必须在4小时内启动调查。

## 复发链

- 新事故可记录`workstation`（工位编号）与`injury_cause`（伤害原因）。任一缺失时`link_status=pending`（待关联），不能进入调查；通过`POST /api/items/{id}/link`补齐（仅报告阶段，reporter/investigator）后自动评估。
- 评估查找近180天同工位同伤害原因的已结案事故，产出复发次数、上次纠正措施摘要（最近一次关联事故中kind为`corrective_action`等纠正类记录）与关联事故列表。
- 每复发一次优先级+2（最多+3），且复发事故一律要求升级处理（`escalation_required=true`）。
- 复发信息只写在新事故上，原事故的结论与记录照旧；列表与详情均展示`recurrence.basis`（复发依据）、`last_corrective_summary`（上次措施）与`linked_items`（关联事故）。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
