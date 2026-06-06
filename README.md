# CHWL Agent — 本地生活短时活动规划与执行 Agent

> 美团黑客松参赛项目 | v4 | 2026.06
> 架构：Agent → Planner → Runtime → Tools | 前端：React + Vite | LLM：DeepSeek

---

## 项目简介

用户一句自然语言（"下午带老婆孩子出去玩几个小时，别太远"），Agent 自动完成：

1. **理解意图** → LLM 解析场景、人数、时间、饮食偏好
2. **查询数据** → 并行搜索活动、餐厅、天气、路线、排队
3. **规划行程** → LLM 评分 + 6 条合理性校验 → 生成时间线
4. **一键履约** → 并行预约、取号、模拟支付
5. **后台监控** → 7x24 盯排队、天气、预约余量
6. **异常重规划** → 排队暴增/下雨/用户疲劳 → 自动调整 + 用户确认

---

## Agent 架构设计

```
┌─ User Interface Layer ────────────────────────┐
│  Web UI (React) ↔ FastAPI (/agent/*)         │
│  chat_demo.py (CLI LLM 对话)                  │
│  cli_demo.py (CLI 命令行调试)                  │
├─ Agent Layer ─────────────────────────────────┤
│  agent/loop.py          Agent 主循环          │
│    ├─ start_session()    启动会话 + Phase 1   │
│    ├─ confirm_itinerary() 确认 + Phase 2      │
│    ├─ handle_sentiment()  情绪触发 + Phase 3  │
│    └─ cancel_session()    取消 + 资源释放     │
│  agent/llm_client.py     DeepSeek/Anthropic   │
├─ Planner Layer ──────────────────────────────┤
│  planner/llm_planner.py  LLM 推理规划器       │
│    ├─ handle_user_context()  意图提取         │
│    ├─ handle_candidates_score() POI 评分      │
│    ├─ handle_itinerary_generate() 行程生成    │
│    └─ handle_itinerary_replan() 重规划        │
│  planner/prompts.py      所有 System Prompt   │
├─ Runtime Layer ──────────────────────────────┤
│  runtime/state_machine.py    行程7态+节点10态 │
│  runtime/event_bus.py        异步事件发布订阅  │
│  runtime/background_watch.py 排队/天气/预约监控│
│  runtime/confirmation_gateway.py 用户确认网关  │
├─ Execution Layer ────────────────────────────┤
│  execution/validator.py   结构化输出校验      │
│  execution/repair.py      JSON 修复          │
├─ Tools Layer ────────────────────────────────┤
│  tools/registry.py        工具注册表          │
│  tools/discovery/         发现类工具          │
│  memory/store.py          三级记忆仓库        │
└──────────────────────────────────────────────┘
```

---

## 目录结构

```
meituan-agent/
│
├── agent/                       # Agent 循环与 LLM 客户端
│   ├── loop.py                  Agent 主循环（Phase 1/2/3 + Fallback）
│   └── llm_client.py            DeepSeek API 客户端（支持 chat/chat_json）
│
├── planner/                     # LLM 推理规划
│   ├── llm_planner.py           LLMPlanner 类（意图提取/评分/生成/重规划）
│   └── prompts.py               所有 System Prompt 常量
│
├── runtime/                     # 运行时基础设施
│   ├── state_machine.py         行程 FSM（7 态）+ 节点 FSM（10 态）
│   ├── event_bus.py             pub/sub 事件总线
│   ├── background_watch.py      排队/天气/预约/余量 7x24 监控
│   └── confirmation_gateway.py  用户确认网关（超时自动拒绝）
│
├── execution/                   # 执行与校验
│   ├── validator.py             结构化输出校验（JSON Schema + 3 次降级）
│   └── repair.py                JSON 修复（5 种修复策略）
│
├── memory/                      # 记忆系统
│   └── store.py                 三级记忆仓库（session_facts / confirmed / derived）
│
├── tools/                       # 工具系统
│   ├── registry.py              工具注册表（invoke / invoke_parallel / retry / circuit breaker）
│   └── discovery/               发现类工具（activities / restaurants / weather）
│
├── api/                         # API 层
│   └── app.py                   FastAPI 入口（/agent/* + /api/* 双路由）
│
├── schemas/                     # 数据契约
│   ├── __init__.py              6 组前端 JSON Schema
│   └── models.py                Pydantic 数据模型
│
├── mocks/                       # Mock 数据
│   ├── __init__.py              MockBackend（11 个 handler）
│   ├── env_simulator.py         环境事件模拟器
│   └── teammate_adapter.py      队友 HTTP API 适配
│
├── scripts/                     # 入口脚本
│   ├── chat_demo.py             LLM 对话模式
│   ├── cli_demo.py              命令行调试模式
│   └── test_llm.py              LLM 集成测试
│
├── frontend/                    # 前端（React + Vite）
│   └── src/
│       ├── api/agentClient.js   API 客户端
│       ├── ChatPage.jsx         聊天主界面
│       └── components/          组件（ItineraryCards / MonitorPanel / ShareModal ...）
│
├── tests/                       # 112 个 pytest
├── docs/                        # 设计文档 / PRD / 审计报告
├── docker-compose.yml           # 一键启动
├── .env.example                 # 环境变量模板
└── start.ps1                    # 启动脚本
```

---

## 工具调用编排

### 11 个 Mock API 工具

| 工具名 | 原 URL | 调用时机 | 类型 |
|--------|--------|---------|------|
| `location` | `/api/location/current` | Phase 1 并行 | Discovery |
| `user_context` | `/api/user/context` | Phase 1 并行 | Planner(LLM) |
| `weather` | `/api/weather` | Phase 1 并行 | Discovery |
| `activities_search` | `/api/activities/search` | Phase 1 并行 | Discovery |
| `restaurants_search` | `/api/restaurants/search` | Phase 1 并行 | Discovery |
| `candidates_score` | `/api/candidates/score` | Phase 1 串行 | Planner(LLM) |
| `route_check` | `/api/route/check` | Phase 1 串行 | Discovery |
| `itinerary_generate` | `/api/itinerary/generate` | Phase 1 最后 | Planner(LLM) |
| `booking_execute` | `/api/booking/execute` | Phase 2 并行 | Execution |
| `booking_status` | `/api/booking/status` | Phase 2 轮询 | Monitoring |
| `itinerary_replan` | `/api/itinerary/replan` | Phase 3 触发 | Planner(LLM) |

### Phase 1 编排（规划）

```
用户输入
  ↓
[并行批次] location + weather + user_context(LLM) + activities + restaurants
  ↓
[串行] candidates_score(LLM) → route_check → itinerary_generate(LLM)
  ↓
[校验] 6 条合理性规则：通勤比例/活动时间/交通匹配/意图确认/节奏/天气
  ↓
状态机: draft → pending_confirm
```

### Phase 2 编排（履约）

```
用户确认
  ↓
状态机: pending_confirm → executing
  ↓
[并行批次] 所有节点 booking_execute（独立协程）
  ↓
[轮询] 每个节点 booking_status（queued → processing → confirmed）
  ↓
[后台启动] background_watch（排队 + 天气 + 预约监控）
  ↓
全部成功 → completed | 部分失败 → needs_replan
```

### Phase 3 编排（重规划）

```
异常触发（排队暴增/天气变化/用户疲劳）
  ↓
识别保护节点（completed_lock + user_pinned）
  ↓
调用 itinerary_replan(LLM)
  ↓
用户确认（confirmation_gateway，超时 120s 自动拒绝）
  ↓
应用新方案或保持原方案
```

### 工具调用安全

- **重试**：临时错误（timeout/network_error）自动重试 2 次，指数退避
- **熔断**：连续 5 次失败 → 断开 30 秒
- **幂等**：已 confirmed 的节点不重复执行
- **确认**：涉及取消/替换有成本节点 → 弹窗 → 用户确认

---

## Mock 数据说明

| 数据 | 数量 | 位置 |
|------|------|------|
| 家庭活动 | 8 | `mocks/__init__.py` |
| 家庭餐厅 | 12 | `mocks/__init__.py` |
| 朋友活动 | 4 | `mocks/__init__.py` |
| 朋友餐厅 | 6 | `mocks/__init__.py` |
| 散步点 | 3 | `mocks/__init__.py` |
| 路线数据 | 11 对 | `mock_api/mock_data/route_data.py` |
| 天气预设 | 2（晴/雨） | `mocks/__init__.py` |

所有 POI 为北京望京商圈数据，支持家庭/朋友双场景。Mock API 队友版本在 `mock_api/` 目录。

---

## 快速开始

### 后端

```bash
cd C:\Users\hgghhy\meituan-agent
venv\Scripts\activate
pip install -r requirements.txt
python -X utf-8 api\app.py
# → http://localhost:8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### CLI 调试

```bash
python scripts/chat_demo.py    # LLM 对话模式
python scripts/cli_demo.py      # 命令行调试
```

### 测试

```bash
pytest tests/ -v
# 112 个测试
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端框架 | FastAPI + asyncio |
| LLM | DeepSeek（通过 httpx 直连） |
| 数据校验 | Pydantic v2 |
| 前端 | React 18 + Vite |
| 测试 | pytest + pytest-asyncio |
| 部署 | docker-compose |
