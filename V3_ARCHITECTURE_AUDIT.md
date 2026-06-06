# v3 分支架构审计报告

> 仓库：https://github.com/orchidlemon/chwl-agent/tree/v3
> 审计日期：2026-06-05
> 审计方法：目录结构分析 + 关键文件通读 + 与标准 Agent 架构对比

---

## 一、目录树

```
chwl-agent (v3)/
├── backend/                     # 后端（单体 flat 结构）
│   ├── main.py                  # FastAPI 入口（全部路由）
│   ├── session.py               # 会话管理
│   ├── orchestrator.py          # Agent 主循环
│   ├── schemas.py               # 数据模型
│   ├── prompts.py               # LLM Prompts
│   ├── skills.py                # Agent Skills（意图解析/评分/规划）
│   ├── tools.py                 # 工具实现（天气/路线/队列等）
│   ├── agent_tools.py           # Agent 调用工具
│   ├── state_machine.py         # 状态机
│   ├── confirmation_gateway.py  # 确认网关
│   ├── background_watch.py      # 后台监控
│   ├── output_validator.py      # 输出校验
│   ├── team_agent_adapter.py    # 队友适配器
│   ├── tool_registry.py         # 工具注册表
│   ├── .env.example
│   └── requirements.txt
├── frontend/                    # 前端（React + Vite）
│   ├── src/
│   │   ├── App.jsx              # 主应用
│   │   ├── ChatPage.jsx         # 聊天页面
│   │   ├── api/agentClient.js   # API 客户端
│   │   └── components/
│   │       ├── ChatInput.jsx        # 聊天输入
│   │       ├── ChatMessage.jsx      # 聊天气泡
│   │       ├── ItineraryCards.jsx   # 行程卡片
│   │       ├── ItinerarySheet.jsx   # 行程面板
│   │       ├── MonitorPanel.jsx     # 监控面板
│   │       ├── ProgressCard.jsx     # 进度卡片
│   │       ├── TransitBar.jsx       # 交通条
│   │       ├── ShareModal.jsx       # 分享弹窗
│   │       ├── UserProfilePanel.jsx # 用户档案
│   │       └── AppRedirectModal.jsx # 重定向弹窗
│   ├── index.html
│   ├── vite.config.js
│   └── package.json
├── mock_api/                    # 队友 Mock API（独立服务）
├── chwl-agent-main/             # 文档副本
├── chwl-agent2/                 # 旧版项目副本
├── start.ps1                    # 启动脚本
└── .gitignore
```

---

## 二、当前架构类型判断

| 维度 | 判断 | 依据 |
|------|------|------|
| 架构模式 | **Flat Backend + SSE 流** | 所有后端文件在 `backend/` 同一层，无分层目录 |
| Agent 模式 | **Phase-aware State Machine** | `run_chat` 方法根据 `phase` 状态做分支判断（gathering/planning/confirming/fulfilling/monitoring） |
| 通信模式 | **SSE 流式响应** | 所有 long-running 操作（chat/plan/fulfill）都返回 `StreamingResponse` |
| 工具体系 | **薄工具层** | `tools.py` 直接调用 teammate API，`agent_tools.py` 是路由层 |
| 记忆系统 | **字典内存** | `session.py` 的 `SessionManager` 用 dict 管理，无独立记忆层 |

**结论：Phase-aware State Machine + SSE Stream 架构，介于 ReAct 和 Workflow Agent 之间。**

---

## 三、架构问题审计

### 3.1 Agent 职责划分

| 组件 | 实际职责 | 问题 |
|------|---------|------|
| `orchestrator.py` | Agent 循环 + Phase 路由 + 规划 + 履约 + 重规划 | **职责过重**，一个文件处理所有阶段 |
| `skills.py` | 意图解析 + 评分 + 规划 | 与 `prompts.py` 职责重叠，skills 调用 prompts |
| `agent_tools.py` | Agent 调用工具的中介 | 名称误导，实质是 "agent → tool" 路由 |
| `tools.py` | 具体工具实现（天气/路线/队列） | 与 `agent_tools.py` 职责边界模糊 |

**问题 1：orchestrator.py 是上帝对象**
- 包含：chat 路由、plan、fulfill、exception confirm、node checkin、report、simulator advance、queue advice
- 约 500+ 行，且不断增长
- `run_chat` 方法包含所有 phase 分支（gathering/planning/confirming/fulfilling/monitoring）

**问题 2：skills.py 和 prompts.py 职责重叠**
- `skills.py` 调用 LLM 做意图解析/评分/规划
- `prompts.py` 只存 prompt 字符串
- 但 `skills.py` 同时也包含了 prompt 选择逻辑
- 应该把 prompt 选择逻辑放到 planner/

### 3.2 目录结构问题

| 问题 | 具体表现 |
|------|---------|
| **同类模块分散** | `state_machine.py` 在 backend/，但 `background_watch.py`、`confirmation_gateway.py` 也在同一层，没有分层 |
| **命名不统一** | `tools.py` vs `agent_tools.py` vs `tool_registry.py`，三个文件都有"tool"但职责不同 |
| **分层不清晰** | 所有文件都在 `backend/`，没有 core/orchestrator/execution 的分层 |
| **Mock 耦合** | `tools.py` 直接调用 teammate API，无 mock 切换层 |

### 3.3 Tool 体系分析

当前工具分类：

```
tools.py（直接 API 调用）
├── get_weather()            → 调用 teammate API
├── get_route()              → 调用 teammate API  
├── get_queue_status()       → 调用 teammate API
├── get_booking_status()     → 调用 teammate API
├── dispatch_taxi()          → 模拟调用

agent_tools.py（Agent 工具路由）
├── call_tool_function()     → 根据 tool_name 分发到 tools.py
├── get_available_tools()    → 返回可用工具列表

tool_registry.py（注册表）
├── ToolRegistry class       → 工具注册/调用
├── register / invoke / invoke_parallel
```

**缺少的关键工具类型：**

| 工具类型 | 职责 | 当前状态 |
|---------|------|---------|
| Discovery Tool | 搜索/发现 POI、活动、餐厅 | 混在 `skills.py` 中 |
| Validation Tool | 校验数据合法性、营业时间、距离 | 无独立实现 |
| Execution Tool | 预约、取号、支付 | 混在 `orchestrator.py` 中 |
| Monitoring Tool | 轮询排队、天气、状态 | `background_watch.py` 独立但和其他工具解耦 |

### 3.4 Execution Layer 闭环分析

```
用户目标 → Planning → Tool Calling → Execution → Confirmation → Replanning
```

| 环节 | 状态 | 断点说明 |
|------|------|---------|
| 用户目标 → Planning | 🟡 正常 | `run_chat` 中 phase=gathering → 调 skills.py 规划 |
| Planning → Tool Calling | 🔴 **断点** | planning 结果直接返回前端，前端调 `/agent/{id}/fulfill` 触发执行，planning 和 execution 通过 frontend 中转 |
| Tool Calling → Execution | 🟡 正常 | `run_fulfill` 后端执行 |
| Execution → Confirmation | 🔴 **断点** | 无内置 confirmation flow，由前端 `/confirmation/resolve` 单独触发 |
| Confirmation → Replanning | 🔴 **断点** | replan 在后端 `run_chat` 中由关键词触发（`REPLAN_WORDS`），无后端主动重规划 |

**关键断点：planning 和 execution 通过前端中转，不是后端闭环。** 前端收到 plan 事件 → 展示给用户 → 用户点击确认 → 前端调 fulfill → 后端执行。这导致如果前端断连，整个流程中断。

### 3.5 Runtime 分析

| 组件 | 设计 | 评价 |
|------|------|------|
| EventBus | ❌ 无 | 未实现，事件通过 SSE 直接推送 |
| Watcher | ✅ `background_watch.py` | 设计合理，独立轮询 |
| StateMachine | ✅ `state_machine.py` | 基本设计合理 |
| Memory | 🟡 `SessionManager` | 用 dict 管理，无持久化 |

**结论：Runtime 设计偏薄，缺少 EventBus 导致组件间耦合度高。**

### 3.6 前端架构

| 组件 | 职责 | 评价 |
|------|------|------|
| `ChatPage.jsx` | 聊天主界面 + SSE 事件处理 | 合理 |
| `ItineraryCards.jsx` | 行程卡片渲染 | 合理 |
| `MonitorPanel.jsx` | 后台监控面板 | 合理 |
| `ShareModal.jsx` | 分享功能 | 合理 |
| `agentClient.js` | API 客户端 | 合理 |
| `UserProfilePanel.jsx` | 用户信息面板 | 合理 |

前端整体架构合理，组件职责清晰。主要问题是 `ChatPage.jsx` 同时承担了 SSE 事件处理和状态管理，可以考虑拆分。

---

## 四、推荐目录结构

```
chwl-agent/
├── agent/                      # Agent 定义与循环
│   ├── __init__.py
│   ├── loop.py                 # Agent 主循环（← 当前 orchestrator.py run_chat）
│   ├── router.py               # 意图路由（← 当前 orchestrator.py 的 phase 分支）
│   └── identity.py             # Agent 身份定义（← 当前 prompts.py 中分离）
│
├── planner/                    # 规划与推理
│   ├── __init__.py
│   ├── intent.py               # 意图理解（← 当前 skills.py 中）
│   ├── scorer.py               # POI 评分（← 当前 skills.py 中）
│   ├── scheduler.py            # 行程编排（← 当前 skills.py 中）
│   ├── replanner.py            # 重规划
│   └── prompts.py              # 所有 system prompt（← 当前 prompts.py）
│
├── runtime/                    # 运行时
│   ├── __init__.py
│   ├── session.py              # 会话管理（← 当前 session.py）
│   ├── state_machine.py        # 状态机（← 当前 state_machine.py）
│   ├── event_bus.py            # 事件总线（🆕 新增）
│   ├── background_watch.py     # 后台监控（← 当前 background_watch.py）
│   └── confirmation_gateway.py # 确认网关（← 当前 confirmation_gateway.py）
│
├── tools/                      # 工具系统
│   ├── __init__.py
│   ├── registry.py             # 工具注册表（← 当前 tool_registry.py）
│   ├── base.py                 # Tool 基类
│   ├── discovery/              # 发现类工具
│   │   ├── activities.py
│   │   ├── restaurants.py
│   │   └── weather.py
│   ├── execution/              # 执行类工具
│   │   ├── booking.py
│   │   └── taxi.py
│   └── monitoring/             # 监控类工具
│       ├── queue.py
│       └── booking_status.py
│
├── memory/                     # 记忆系统
│   ├── __init__.py
│   ├── store.py                # 记忆存储（← 当前 session.py 中的 memory）
│   ├── session.py              # 会话记忆
│   └── long_term.py            # 长期记忆（🆕）
│
├── execution/                  # 履约层
│   ├── __init__.py
│   ├── fulfillment.py          # 履约执行（← 当前 orchestrator.py run_fulfill）
│   └── validator.py            # 输出校验（← 当前 output_validator.py）
│
├── api/                        # API 层
│   ├── __init__.py
│   ├── app.py                  # FastAPI 入口（← 当前 main.py）
│   ├── routes/
│   │   ├── chat.py
│   │   ├── session.py
│   │   ├── plan.py
│   │   ├── fulfill.py
│   │   ├── confirm.py
│   │   ├── monitor.py
│   │   └── simulator.py
│   └── middleware.py
│
├── frontend/                   # 前端（保持不变）
│   └── src/
│
├── mocks/                      # Mock 数据
│   ├── __init__.py
│   └── handlers.py
│
├── mock_api/                   # 队友 API（保持不变）
│
├── tests/
├── docs/
└── scripts/
```

---

## 五、迁移方案

### P0（必须调整）— 影响 Demo 演示

| # | 问题 | 操作 | 原因 |
|---|------|------|------|
| P0-1 | `orchestrator.py` 200+ 行 run_chat | 将 run_chat 的 phase 分支拆到 `agent/loop.py` + `agent/router.py` | 当前单文件不可维护 |
| P0-2 | `tools.py` 与 `agent_tools.py` 命名混乱 | 合并为 `tools/` 目录，按类型分文件 | 当前命名误导 |
| P0-3 | planning 和 execution 通过前端中转 | 改为后端闭环：plan → auto-fulfill | 前端断连则流程中断 |

### P1（建议调整）— 影响可维护性

| # | 问题 | 操作 | 原因 |
|---|------|------|------|
| P1-1 | 缺少 EventBus | 在 `runtime/` 中新增事件总线 | 组件间耦合度高 |
| P1-2 | `skills.py` + `prompts.py` 职责重叠 | 合并到 `planner/` 目录 | SKill 和 Prompt 的界限在 hackathon 中没必要分离 |
| P1-3 | `state_machine.py` 在 backend/ 根目录 | 移到 `runtime/state_machine.py` | 归类 |
| P1-4 | 前端 `ChatPage.jsx` 过重 | 拆分 SSE 处理到独立 hook | 现在 SSE 和 UI 混在一起 |

### P2（未来优化）— 影响扩展性

| # | 问题 | 操作 | 原因 |
|---|------|------|------|
| P2-1 | Session 用 dict 管理无持久化 | 接入 MemoryStore | 重启后会话丢失 |
| P2-2 | 缺少工具抽象层 | 实现 `tools/base.py` Tool 基类 | 当前工具实现和调用耦合 |
| P2-3 | `confirmation_gateway` 和 `background_watch` 在 backend/ 根目录 | 移到 `runtime/` | 归类 |
| P2-4 | 无 Discovery Tool | 创建 `tools/discovery/` | 提高工具体系完整性 |

---

## 六、总结

### 优点（值得保留）

| 特性 | 说明 |
|------|------|
| **SSE 流式响应** | 所有 long-running 操作用 SSE，前端体验好 |
| **Phase-aware 设计** | phase 状态机（gathering/planning/confirming/fulfilling/monitoring）覆盖了完整生命周期 |
| **前端组件完整** | 行程卡片、监控面板、分享、交通信息组件齐全 |
| **确认网关** | confirmation_gateway 设计合理，支持超时自动拒绝 |
| **后端与独立 Mock API 解耦** | 通过 HTTP 调用 teammate API，便于独立开发 |

### 核心问题

| 问题 | 严重性 |
|------|--------|
| 所有后端代码在 `backend/` 同一目录，无分层 | P0 |
| `orchestrator.py` 是上帝对象（500+行，包含路由+规划+履约+监控） | P0 |
| tools.py / agent_tools.py / tool_registry.py 命名混乱 | P0 |
| planning → execution 通过前端中转，不是后端闭环 | P0 |
| 缺少 EventBus，组件间直接调用 | P1 |
| 无持久化记忆，重启后丢失 | P2 |
