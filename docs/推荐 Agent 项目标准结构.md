# 推荐 Agent 项目标准结构

> 参考：OpenHands、LangGraph Runtime、CrewAI 等现代 Agent 框架
> 用途：作为项目重构的参考架构

---

## 顶层目录布局

```
project/
├── agent/              # Agent 定义与循环
├── planner/            # 规划与推理
├── runtime/            # 运行时与执行引擎
├── tools/              # 工具系统
├── memory/             # 记忆系统
├── api/                # API 层（对外暴露）
├── frontend/           # 前端（可选）
├── schemas/            # 数据契约
├── tests/              # 测试
├── docs/               # 文档
└── config/             # 配置
```

---

## 各层职责

### 1. agent/ — Agent 定义与循环

**职责**：定义 Agent 的身份、行为循环、对话路由。

```
agent/
├── identity.py         # Agent 身份定义（system prompt、人设）
├── loop.py             # Agent 主循环（perceive → think → act → observe）
├── router.py           # 意图路由（用户输入 → action）
└── prompts/            # 所有 system prompt
    ├── butler.py
    ├── planner.py
    └── responder.py
```

**关键设计**：
- `loop.py` 是 Agent 的唯一入口，所有用户输入先到这里
- `router.py` 决定下一步 action（plan/confirm/replan/show_plan 等）
- `prompts/` 与代码分离，修改 prompt 不需要改代码逻辑
- `identity.py` 只有一个职责：告诉 LLM "你是谁"

**在当前项目中的对应**：`scripts/chat_demo.py` 的 `handle_message()` + `AGENT_PROMPT`

---

### 2. planner/ — 规划与推理

**职责**：LLM 驱动的规划核心。接收结构化输入，返回结构化输出。

```
planner/
├── intent.py           # 意图理解（用户输入 → 结构化意图）
├── scorer.py           # POI 评分（候选 → 评分排序）
├── scheduler.py        # 行程编排（POI + 约束 → 时间线）
├── replanner.py        # 重规划（反馈 + 旧方案 → 新方案）
└── constraints.py      # 约束定义与转换
```

**关键设计**：
- 每个模块只做一件事：intent 只解析意图，scorer 只打分，scheduler 只排时间
- planner 不直接调用工具，通过 runtime 调用
- `constraints.py` 把用户反馈转为结构化约束

**在当前项目中的对应**：`core/llm_planner.py` 的各个 handler

---

### 3. runtime/ — 运行时与执行引擎

**职责**：管理执行上下文、状态机、事件驱动、异步任务调度。

```
runtime/
├── context.py          # 会话上下文（session_id + 状态）
├── state_machine.py    # 状态机定义
├── event_bus.py        # 事件总线
├── task_manager.py     # 异步任务调度
└── error_handler.py    # 统一错误处理 + 重试
```

**关键设计**：
- `context.py` 是整个系统的唯一真相源（single source of truth）
- `task_manager.py` 管理所有异步任务（Phase 1/2/3），提供 single-flight lock
- `error_handler.py` 统一处理：retry 一次 → 显式失败，禁止 silent continuation

**在当前项目中的对应**：`orchestrator/orchestrator.py` + `event_bus.py` + `state_machine.py`

---

### 4. tools/ — 工具系统

**职责**：工具注册、调用、重试、熔断。

```
tools/
├── registry.py         # 工具注册表
├── base.py             # ToolDefinition + 调用接口
├── retry.py            # 重试策略
├── circuit_breaker.py  # 熔断器
└── implementations/    # 具体工具实现
    ├── weather.py
    ├── restaurant_search.py
    ├── activity_search.py
    ├── route_calc.py
    ├── booking.py
    └── queue_status.py
```

**关键设计**：
- `registry.py` 只管理"有什么工具"，不管理"什么时候调"
- 每个工具独立文件，实现统一的 `Tool` 接口
- 重试和熔断是横切关注点，通过装饰器/中间件实现，不是写在工具里

**在当前项目中的对应**：`core/tool_registry.py`

---

### 5. memory/ — 记忆系统

**职责**：会话记忆、长期记忆、偏好的存储与检索。

```
memory/
├── store.py            # 记忆仓库接口
├── session.py          # 会话记忆（本轮对话）
├── long_term.py        # 长期偏好（跨会话）
├── derived.py          # 派生偏好（从事件中提取）
└── serializer.py       # 序列化/持久化
```

**关键设计**：
- 三级存储分离：session / confirmed / derived
- 记忆不能被 Planner 直接写入，必须通过 memory API

**在当前项目中的对应**：`core/memory_store.py`

---

### 6. api/ — API 层

**职责**：对外暴露的 REST/SSE/WebSocket 接口。

```
api/
├── routes/
│   ├── plan.py         # POST /api/plan
│   ├── session.py      # GET /api/session/{id}
│   ├── confirm.py      # POST /api/confirm
│   ├── modify.py       # POST /api/modify
│   ├── sentiment.py    # POST /api/sentiment
│   ├── resolve.py      # POST /api/resolve
│   └── events.py       # GET /api/events/{id} (SSE)
├── middleware/
│   ├── auth.py
│   └── error_handler.py
└── app.py              # FastAPI 入口
```

**关键设计**：
- 每个路由独立文件，职责单一
- API 层不包含业务逻辑，只做请求/响应转换
- SSE 和 REST 分离

**在当前项目中的对应**：`scripts/test_server.py`

---

### 7. schemas/ — 数据契约

**职责**：所有跨组件通信的数据结构定义。

```
schemas/
├── itinerary.py        # 行程相关
├── node.py             # 节点相关
├── user.py             # 用户输入相关
├── feedback.py         # 反馈/约束相关
├── fulfillment.py      # 履约相关
├── monitoring.py       # 监控相关
├── sharing.py          # 分享相关
└── enums.py            # 枚举统一管理
```

**关键设计**：
- 所有 Schema 集中在 schemas/ 下，不分散在 core/models.py
- 枚举独立文件，避免循环引用

**在当前项目中的对应**：`core/models.py` + `schemas/__init__.py`

---

## 对比：当前项目 vs 标准结构

| 标准层 | 当前文件 | 差距 |
|--------|---------|------|
| `agent/` | `scripts/chat_demo.py` | 路由、prompt、事件处理都在一个文件里 |
| `planner/` | `core/llm_planner.py` | 5 个 handler 在一个文件里，但 prompt 和逻辑在一起 |
| `runtime/` | `orchestrator/orchestrator.py` + `event_bus.py` + `state_machine.py` | 整体结构合理，但 `orchestrator.py` 太大（1400+行） |
| `tools/` | `core/tool_registry.py` + `mocks/__init__.py` | 工具实现和 mock 数据混在一起 |
| `memory/` | `core/memory_store.py` | 接口合理，但未被实际使用 |
| `api/` | `scripts/test_server.py` | 所有路由在一个文件里 |
| `schemas/` | `core/models.py` + `schemas/__init__.py` | 数据定义分散在两个文件 |

---

## 总结

成熟 Agent 项目的共同特征是**职责分离、单层单向依赖**：

```
agent → planner → runtime → tools
                 ↓
              memory
                 ↓
              api → frontend
```

当前项目的主要差距不是功能缺失，而是**职责混在一起**。改目录结构就能解决的问题，不需要重构代码。
