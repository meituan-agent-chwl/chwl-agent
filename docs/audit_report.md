# 项目审计报告

## 审计方法
- 仅依赖：设计文档原文、代码文件、测试运行结果（102 pass）、chat_demo 运行时输出
- 禁止推测。无证据 = "无法确认"

---

## 第一部分：测试运行结果（证据）

**证据 1：pytest 输出**
- 102 passed in 74.71s
- 8 个测试文件全部通过
- 来源：用户终端输出 2026-06-05

**证据 2：chat_demo 运行时输出**
```
> 下午带老婆孩子出去玩
  . 正在获取当前位置、天气和活动信息...
  . 已找到 3 个活动和 3 个餐厅，正在评分...
  . 正在生成行程方案...
[规划完成] 找到 3 个活动
  - 14:00 乐高探索中心 (120min)
  - 16:10 商场轻松散步区 (40min)
  - 17:00 乐高探索中心餐厅 (60min)
[Agent] 下午的安排是：先去乐高探索中心玩到4点，然后在商场里溜达到4点50，最后在乐高探索中心的餐厅吃晚饭。
```
- 来源：用户终端输出 2026-06-05

---

## 第二部分：按设计文档逐项验收

### 2.1 用户流程（5 阶段）

| 阶段 | 设计要求 | 实现状态 | 证据 |
|------|---------|---------|------|
| Stage 1: 意图唤醒与追问 | 系统不直接甩终版方案，结构化轻量追问（最多 1 轮） | **无法确认** | chat_demo 未展示追问环节，直接进入规划。代码中 `chat_demo.py` handle_message 没有追问逻辑 |
| Stage 1: 快速标签 | 提供快捷标签（2点出发/室内/怕晒） | **未实现** | chat_demo 和 CLI 均无标签选择 |
| Stage 2: CoT 动态展示 | 规划过程实时流式展示，默认折叠 | **部分实现** | chat_demo 展示了 3 行状态流（正在获取位置/已找到候选/正在生成方案），但非流式，且 CoT 内容不是 LLM thought 的真实展示 |
| Stage 2: 草稿态用户操作 | 替换/删除/插入/Pin/延后/跳过/完成打卡 | **部分实现** | orchestrator.py 中 ItineraryModification 支持这些操作，但 chat_demo 中没有暴露给用户。CLI 的 edit 命令标记为"占位" |
| Stage 3: 并行履约 + 进度流水线 | 所有节点并行履约，按时间序展示状态 | **部分实现** | orchestrator.py `_phase2_execute` 并行发起 booking，但 chat_demo 在履约期间没有逐条展示进度（被 15s sleep 替代） |
| Stage 4: 出行看板 | 天气提示、地图模式、核销看板、社交分享 | **未实现** | 无对应代码 |
| Stage 5: 异常感知 + 知情权重规划 | 排队暴增/天气骤变/用户疲劳触发局部重规划，弹窗确认 | **部分实现** | `background_watch.py` 监控队列/天气/预约/余量；`_phase3_replan` 处理用户疲劳。但 watch alert → replan 的链路在 chat_demo 中没有展示 |

### 2.2 工具层设计

| 工具 | 设计要求 | 实现状态 | 证据 |
|------|---------|---------|------|
| 工具总数 | 11 个工具 | **已实现** | `core/tool_registry.py` 注册了 11 个工具定义 |
| 工具调用策略 | 并行调用（天气/活动/餐厅/位置），后置调用（路线/评分） | **已实现** | `orchestrator.py` `_phase1_plan` 先并行 5 路数据采集，后串行评分→路线→生成 |
| 事实层不返回主观判断 | 工具层只返回原始事实数据 | **已实现** | MockBackend handler 返回原始事实，无"推荐""高风险"等标签 |
| 重试机制 | 调用超时 2s 触发重试 | **已实现** | `tool_registry.py` 默认 timeout=5000ms, max_retries=2, 指数退避 |
| 熔断机制 | 连续失败断路器 | **已实现** | `tool_registry.py` _circuit_threshold=5, _circuit_reset_seconds=30 |

### 2.3 规划层设计

| 功能 | 设计要求 | 实现状态 | 证据 |
|------|---------|---------|------|
| 意图解析 | 解析场景/同伴/时间/距离/偏好 | **已实现** | `llm_planner.py` `handle_user_context` 调用 DeepSeek 解析 |
| POI 评分 | 6 维度评分（距离/排队/偏好/舒适/模式/热度） | **已实现** | `llm_planner.py` SCORING_SYSTEM prompt 包含这 6 个维度 |
| 模式差异化评分 | 家庭/朋友场景不同权重 | **已实现** | SCORING_SYSTEM prompt 中区分家庭（child_friendly/排队）和朋友（氛围感） |
| 行程生成 | 主活动→餐厅→轻活动固定结构 | **已实现** | `llm_planner.py` ITINERARY_GENERATION_SYSTEM prompt |
| 合理性校验 | 6 条规则（通勤/活动时间/距离/意图/节奏/天气） | **部分实现** | `orchestrator.py` `_validate_feasibility` 包含全部 6 条规则的检查代码。但：运行态未验证校验是否实际生效；`orchestrator.py:483` 调用校验后不检查结果直接继续 |

### 2.4 Agent 循环设计

| 功能 | 设计要求 | 实现状态 | 证据 |
|------|---------|---------|------|
| LLM 驱动的 Agent | DeepSeek 驱动意图理解、评分、生成 | **已实现** | `llm_planner.py` 三个 handle 方法均调用 DeepSeek |
| 对话回复生成 | LLM 生成管家风格回复 | **已实现** | `llm_planner.py` `generate_response` 方法，在 chat_demo 运行输出中可见 |
| 感知→思考→行动循环 | Agent 周期性地感知环境变化 | **无法确认** | chat_demo 运行输出只展示了单轮交互。未验证多轮对话中 Agent 能否正确处理上下文 |

### 2.5 记忆层设计

| 功能 | 设计要求 | 实现状态 | 证据 |
|------|---------|---------|------|
| 三级记忆仓库 | session_facts / confirmed_preferences / derived_preferences | **已实现** | `core/memory_store.py` 实现三级存储，含 TTL/promote/序列化 |
| 对话历史 | 存储对话历史用于 LLM 上下文 | **未实现** | chat_demo.py 中 conversation_history 列表存在但未被使用（`handle_message` 未传入 LLM） |
| 长期偏好持久化 | 用户授权后保存跨会话偏好 | **未实现** | `memory_store.py` 支持磁盘持久化，但 chat_demo 中没有调用 save() |

### 2.6 数据模型完整性

| 模型 | 字段完整性 | 证据 |
|------|-----------|------|
| ItineraryData | 与 PRD 定义一致 | `core/models.py` 包含所有 PRD 字段 |
| ItineraryNode | 与 PRD 定义一致 | `core/models.py` 包含所有 PRD 字段（node_id/poi_id/status/completed_lock/user_pinned/soft_lock/conflicts） |
| 枚举类型 | 6 种枚举 | `core/models.py` SceneType/CompanionType/NodeCategory/ResourceType/ModeType |

---

## 第三部分：差异清单

### P0 功能差异（应实现但未实现或有缺陷）

| # | 功能 | 设计要求 | 实际状态 | 缺陷等级 |
|---|------|---------|---------|---------|
| G1 | 数据量不足 | PRD 要求 >= 8 个家庭场所、>= 12 家餐厅 | MockBackend 仅 3 个活动 + 3 个餐厅 | **严重** |
| G2 | LLM 胡编 POI | 设计要求"禁止 LLM 编造不在 Mock 池中的 POI" | chat_demo 输出显示"乐高探索中心餐厅"为 Mock 池不存在的 POI | **严重** |
| G3 | 校验不通过不阻止输出 | PRD 5.3："任何一条不通过则方案不得输出" | `orchestrator.py:483` 调用 `_validate_feasibility` 后不检查 `fc.passed`，继续输出 | **中等** |
| G4 | 对话历史未传给 LLM | Agent 应有上下文记忆 | `chat_demo.py` `conversation_history` 未传入 LLM | **中等** |
| G5 | 无追问环节 | PRD 3.2：结构化轻量追问最多 1 轮 | chat_demo 直接规划，没有追问过程 | **中等** |
| G6 | 履约进度不可见 | 设计文档：按时间序展示逐条状态 | chat_demo 的 `_do_execute` 用 `asyncio.sleep(15)` 替代实时展示 | **中等** |
| G7 | 资源损失警告未展示 | PRD 9.1：取消有成本节点前弹窗 | `cancel_session` 虽发出 `resource_loss_warning` 事件，但 chat_demo 未展示给用户 | **低** |
| G8 | 社交分享未实现 | P0 需求 | 仅 Schema 定义，无生成逻辑 | **低** |

### 测试覆盖差异

| # | 范围 | 状态 | 证据 |
|---|------|------|------|
| T1 | LLM 层测试 | **无** | 无测试覆盖 `llm_client.py` 或 `llm_planner.py` |
| T2 | 端到端集成测试 | **无** | 无测试覆盖从用户输入到行程完成的完整链路 |
| T3 | 异常场景测试 | **无** | 无测试覆盖网络超时、API 失败、LLM 返回异常 |

---

## 第四部分：运行验证结果

### 4.1 已验证通过的功能

| 功能 | 验证方式 | 结果 |
|------|---------|------|
| 状态机转换 | pytest 17 个测试 | 全部通过 |
| 工具注册与调用 | pytest 6 个测试 | 全部通过 |
| 并行数据采集 | pytest 12 个 Orchestrator 测试 | 全部通过 |
| 三级记忆读写 | pytest 16 个测试 | 全部通过 |
| JSON 修复与校验 | pytest 18 个测试 | 全部通过 |
| 后台监控启停 | pytest 9 个测试 | 全部通过 |
| 环境模拟器 | pytest 9 个测试 | 全部通过 |
| 多候选替代 | pytest 5 个测试 | 全部通过 |
| LLM 意图理解 | chat_demo 运行输出：正确识别家庭场景 | 通过 |
| LLM 行程生成 | chat_demo 运行输出：生成 3 个节点 | 通过 |

### 4.2 已验证发现的问题

| 问题 | 证据 |
|------|------|
| LLM 编造不存在的 POI | chat_demo 输出："乐高探索中心餐厅"——该 POI ID 不在 MockBackend 的 `MOCK_RESTAURANTS_FAMILY` 列表（`mocks/__init__.py:114-151`）中 |
| Mock 数据只有 3+3 | `mocks/__init__.py:83-168` 定义了 3 个活动 + 3 个餐厅，PRD 要求 >=8 个场所、>=12 家餐厅 |
| 履约期间无进度展示 | `chat_demo.py:_do_execute` 第 150 行使用 `await asyncio.sleep(15)` 硬等，而非实时展示 booking 状态 |

---

## 第五部分：最关键问题列表

### 阻塞上线问题

| 优先级 | 问题 | 影响 | 建议修复 |
|--------|------|------|---------|
| **P0** | LLM 编造 POI | 用户看到不存在的餐厅，破坏信任 | 在 `llm_planner.py` 的 `handle_itinerary_generate` 和提示词中要求 LLM 只能从提供的候选 POI 列表中选择，并在生成后做 POI ID 合法性校验 |
| **P0** | Mock 数据 3+3 远低于 PRD 要求 | 演示时方案单一，无法体现 LLM 推理能力 | 补充 Mock 数据到至少 8+12 |
| **P0** | Feasibility check 不通过不阻止输出 | 违反 PRD 核心原则 | `orchestrator.py:483` 后增加 `if not fc.passed: return` |
| **P1** | LLM 无测试覆盖 | 修改 LLM prompt 可能无声破坏功能 | 给 `llm_planner.py` 三个 handler 加测试 |
| **P1** | 对话历史未传入 LLM | 多轮对话中 Agent 无法感知上下文 | `chat_demo.py` `handle_message` 调用 LLM 时传入 `self.conversation_history` |
| **P2** | 无端到端集成测试 | 无法快速验证改动的整体影响 | 加一个从 start_session → get_status → confirm → 验证完成的联合测试 |

---

## 附录：审计范围

- 审计时间：2026-06-05
- 设计文档版本：PRD v3（mvp_prd_v3.md）+ 架构设计文档（本地短时活动规划与执行 Agent 设计文档）
- 代码版本：meituan-agent 项目当前文件
- 测试运行：102 passed / 0 failed
- 运行时验证：chat_demo.py 单轮对话
- 未验证项（需人工确认）：多轮对话、履约进度展示、资源损失警告弹窗、社交分享、地图模式
