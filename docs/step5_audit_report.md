# 第五步：审计报告

---

## 一、审计范围与方式

| 项目 | 内容 |
|------|------|
| 审计对象 | meituan-agent 项目全部 35 个 Python 文件 |
| 设计对照 | mvp_prd_v3.md + 架构设计文档 + mock_api_design.md + agent_prompt_skill_design.md |
| 测试验证 | 110 个 pytest（全部通过）|
| 运行验证 | cli_demo.py + chat_demo.py 实际运行输出 |
| 审计原则 | 不推测，每项结论附带证据文件+行号+运行结果 |

---

## 二、已验证通过的（基石稳定的部分）

| 模块 | 测试数 | 运行证据 |
|------|--------|---------|
| 状态机引擎（行程7态+节点10态） | 17 pass | test_state_machine.py |
| 工具注册/调用/重试/熔断 | 6 pass | test_tool_registry.py |
| 数据模型（完整字段） | 纳入 orchestrator | models.py 全字段覆盖 |
| 三级记忆仓库 | 19 pass | test_memory_store.py |
| 6 组前端 JSON Schema | 已定义 | schemas/__init__.py |
| Orchestrator 三层流程 | 12 pass | test_orchestrator.py |
| 5 级 Fallback | 代码完整 | orchestrator.py L841 |
| 后台监控四维 | 9 pass | test_background_watch.py |
| JSON 修复+输出校验 | 25 pass | test_output_validator.py |
| 环境模拟器 | 9 pass | test_env_simulator.py |
| LLM 驱动规划 | 8 pass (新) | test_llm_planner.py (DeepSeek 已连通) |
| Mock 数据 8活动+12餐厅 | 已满足 | chat_demo 运行时日志 |

---

## 三、最关键问题列表

### P0 — 阻塞上线（必须修复）

| # | 问题 | 严重性 | 发现方式 | 证据 |
|---|------|--------|---------|------|
| **B1** | chat_demo 履约完成后状态锁死，所有后续输入返回同一句话 | **阻塞** | 运行验证 | chat_demo 输出："> 老婆在减肥" / "> 孩子累了" / "> 看看方案" 全部返回"全部预约好了" |
| **B2** | LLM 生成的行程缺餐厅节点（设计：主活动→餐厅→轻活动） | **阻塞** | 运行验证 | chat_demo 输出："14:00 乐高乐园 (120min)" → "16:15 商场轻松散步区"，无餐厅节点 |
| **B3** | chat_demo 缺乏询问环节，用户偏好变更无法传递 | **阻塞** | 运行验证 | 用户说"老婆在减肥 不想吃太油腻的"，系统忽略 |
| **B4** | Phase 3 重规划后 handle_message 无对应分支 | **阻塞** | 代码审计 | chat_demo.py handle_message 无 `elif state == "needs_replan"` 分支 |
| **B5** | conversation_history 未传入 LLM，多轮对话无上下文 | **阻塞** | 代码审计 | chat_demo.py L48 变量存在，handle_message 未使用 |
| **B6** | G1 追问环节缺失，直接规划未收集偏好 | **阻塞** | 设计对照 | chat_demo 无追问逻辑，设计文档要求"最多1轮追问" |
| **B7** | 朋友餐厅数据不足（3<6条），路线数据不足（4<10对） | **阻塞** | 数据审计 | mocks 代码 vs 设计目标 |

### P1 — 严重（影响体验但可绕行）

| # | 问题 | 证据 |
|---|------|------|
| **B8** | 草稿态操作未暴露（替换/删除/Pin） | ItineraryModification 模型支持，但 chat_demo/cli_demo 未实现交互 |
| **B9** | 履约进度无实时展示（用 sleep 15 替代） | chat_demo.py _do_execute 第 276-283 行 |
| **B10** | 资源损失警告未展示给用户 | cancel_session 发出 resource_loss_warning 事件，但无人消费 |
| **B11** | Feasibility check 阻断已加代码未运行验证 | orchestrator.py L482-496 |

### P2 — 一般（后续迭代）

| # | 问题 |
|---|------|
| B12 | 社交分享未实现（只有 Schema） |
| B13 | 评测体系未实现 |
| B14 | 地图联动/核销看板未实现 |

---

## 四、运行验证拦截的 4 个 Bug

这 4 个 Bug 是在 chat_demo 实际运行中发现的，测试未能覆盖：

| Bug | 表现 | 根因 | 发现时间 |
|-----|------|------|---------|
| 履约完成后状态锁死 | 所有输入返回"全部预约好了" | handle_message 无 `needs_replan` 分支 + completed 分支关键词匹配过于狭窄 | 2026-06-05 |
| 方案缺餐厅节点 | 只有 2 个节点，缺失餐厅 | LLM prompt ITINERARY_GENERATION_SYSTEM 对节点数量要求不够明确 | 2026-06-05 |
| 偏好更新被忽略 | "老婆在减肥"无响应 | 履约完成后状态锁死，LLM 未收到新约束 | 2026-06-05 |
| 重规划后无法查看结果 | "调整结果呢"被忽略 | handle_message 无 needs_replan 状态处理 | 2026-06-05 |

---

## 五、推荐下一步开发顺序

### Phase 1：修阻塞 Bug（立即）

| 顺序 | 修复内容 | 涉及文件 | 预计工作量 |
|------|---------|---------|----------|
| 1 | chat_demo handle_message 增加 `needs_replan` 分支 + 放宽 completed 分支条件 | `scripts/chat_demo.py` | 小（~20 行） |
| 2 | LLM prompt 要求必含餐厅节点 | `core/llm_planner.py` ITINERARY_GENERATION_SYSTEM | 小（~5 行） |
| 3 | conversation_history 传入 LLM | `scripts/chat_demo.py` handle_message | 小（~10 行） |
| 4 | 补充朋友餐厅数据（3→6）+ 路线数据（4→10） | `mocks/__init__.py` + `mock_api/route_data.py` | 中（~50 行） |

### Phase 2：补齐交互体验（1-2 天）

| 顺序 | 修复内容 | 涉及文件 |
|------|---------|---------|
| 5 | 实现追问环节（Scene 2 的 Structured Lightweight Question） | `scripts/chat_demo.py` + `core/llm_planner.py` |
| 6 | 履约进度实时展示（替代 sleep 15） | `scripts/chat_demo.py` _do_execute |
| 7 | 资源损失警告展示 | `scripts/chat_demo.py` 订阅 resource_loss_warning 事件 |

### Phase 3：加固测试（1 天）

| 顺序 | 修复内容 |
|------|---------|
| 8 | 端到端集成测试（start_session → confirm → verify completed） |
| 9 | Feasibility check 阻断测试 |
| 10 | POI 合法性校验测试 |

### Phase 4：前端对接

| 顺序 | 修复内容 |
|------|---------|
| 11 | 前端接入 FastAPI test_server，使用 SSE 事件流 |
| 12 | 6 组 Schema 对接前端渲染 |
| 13 | 草稿态操作交互（替换/删除/Pin） |

---

## 六、结论

当前项目状态：**Orchestrator 层核心稳定，chat_demo（对话层）有 4 个阻塞 Bug，LLM 已连通。**

| 维度 | 评分 | 说明 |
|------|------|------|
| 核心引擎可靠性 | **稳定** | 110 tests pass |
| 设计目标覆盖度 | **~65%** | P0 功能 9/16 完全实现 |
| 运行时可用性 | **不可用** | chat_demo 有 4 个阻塞 Bug |
| LLM 集成 | **已连通** | DeepSeek 8/8 测试通过 |
| 数据完整性 | **基本满足** | 朋友餐厅/路线数据不足 |
| 前端对接 | **未开始** | Schema 就绪，FastAPI 服务就绪 |

**一句话**：修完 B1-B4（chat_demo 的 4 个阻塞 Bug）后，LLM 对话模式可正常演示。修完 B5-B7 后可做产品级验收。
