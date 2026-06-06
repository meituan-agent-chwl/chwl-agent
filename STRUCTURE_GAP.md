# 当前 v4 结构 vs 理想结构 差距分析

## 当前 v4 结构

```
meituan-agent/
├── agent/                   ✅ 存在，但有缺失
├── planner/                 ⚠️ 只有 llm_planner.py 一个文件
├── runtime/                 ✅ 基本完整
├── execution/               ⚠️ 只有 validator/repair，缺少 fulfill
├── memory/                  ✅ 完整
├── tools/                   ⚠️ 只有 registry.py
├── api/                     ⚠️ app.py 在，routes/ 是空的
├── schemas/                 ✅ 完整（__init__ + models）
├── scripts/                 ✅
├── tests/                   ✅
├── docs/                    ✅
├── mocks/                   ✅
│
├── core/                    ❌ 应该删除（文件已迁移）
├── orchestrator/            ❌ 应该删除（文件已迁移）
│
├── frontend/                ← 不在本项目内（在前端仓库）
├── mock_api/                ← 不在本项目内（队友的）
│
├── docker-compose.yml       ❌ 缺失
├── .env.example             ❌ 缺失
└── start.ps1                ✅
```

## 差距清单

### P0 — 必须修

| 差距 | 当前 | 理想 | 影响 |
|------|------|------|------|
| `core/` 目录残留 | 还有文件没删 | 不存在 | `core` 不是标准层名 |
| `orchestrator/` 目录残留 | 还有文件没删 | 不存在 | 同上 |
| 缺少 `docker-compose.yml` | 无 | 一键启动三个服务 | 评委演示不方便 |

### P1 — 建议修

| 差距 | 当前 | 理想 |
|------|------|------|
| `planner/` 未拆分 | 一个 `llm_planner.py` 500 行 | 分成 `intent/scorer/scheduler/replanner/prompts` 五个文件 |
| `tools/` 只有 registry | 一个 `registry.py` | 应有 `discovery/activities.py`、`restaurants.py`、`weather.py` |
| `api/routes/` 是空的 | 路由全在 `app.py` | 每个路由独立文件 |
| `execution/` 缺 `fulfill.py` | 履约逻辑还在 `agent/loop.py` 里 | 拆到 `execution/fulfill.py` |
| 缺少 `agent/router.py` | 意图路由在 `chat_demo.py` 里 | 拆到 `agent/router.py` |

### P2 — 有空再修

| 差距 | 说明 |
|------|------|
| `.env.example` | 环境变量模板，方便新开发者 |
| `tools/discovery/` 拆分 | 把 MockBackend 的数据查询功能抽到独立文件 |
| `execution/monitoring/` | 监控类工具独立 |

## 当前要不要继续改？

当前 v4 分支已经完成了**最关键的**：文件搬到了正确的分层目录，import 全更新，66+28 测试通过。`core/` 和 `orchestrator/` 残留的主要是 `__init__.py` 和一些旧引用文件，删掉不影响运行。

剩下的 P1 差距（planner 拆分、tools 拆分、api 路由拆分）本质上是**拆文件**，不改逻辑。可以现在做，也可以等 hackathon 结束后再做。

你决定：继续改到完全匹配理想结构？还是当前就推了？
