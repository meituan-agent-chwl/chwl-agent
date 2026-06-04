# 第三步：差异清单

逐项对比设计目标 vs 实际实现。每项包含：设计目标、实现现状、差异等级。

---

## 一、功能差异（P0/P1/P2）

### P0 级差异

| # | 功能 | 设计目标 | 实际现状 | 差异 | 等级 |
|---|------|---------|---------|------|------|
| G1 | 意图理解与追问 | Stage 1：结构化轻量追问最多 1 轮，提供快捷标签 | chat_demo 直接规划，无追问环节，无标签 | **未实现** | P0 |
| G2 | 草稿态操作 | 替换/删除/插入/Pin/延后/跳过/完成打卡，上限 5 次 | `ItineraryModification` 模型支持，但 chat_demo/cli_demo 未暴露给用户 | **未暴露** | P0 |
| G3 | 卡片三级状态 | 正常/风险/严重冲突三级视觉状态 | schema 定义了 CardStatus 枚举，但无运行证据 | **未验证** | P0 |
| G4 | 异常重规划+知情权弹窗 | 排队暴增/天气/疲劳触发，弹窗确认后才执行 | Phase3 和 ConfirmationGateway 已实现，但 chat_demo 履约为 sleep 15 无弹窗 | **部分实现** | P0 |
| G5 | Feasibility check 阻断 | 任何一条不通过则方案不得输出 | 代码已在 orchestrator.py L482-496 加了阻断，但未运行验证 | **代码已加未运行** | P0 |
| G6 | 资源损失警告 | 取消有成本节点时告知损失，二次确认 | cancel_session 发出 resource_loss_warning 事件，但 chat_demo 不展示给用户 | **事件发出未展示** | P0 |
| G7 | LLM 不编造 POI | 禁止编造不在 Mock 池中的 POI | llm_planner.py 加了 POI ID 过滤，但 LLM 不通无法验证 | **代码已加未验证** | P0 |
| G8 | CoT 展示 | 规划过程实时流式展示，默认折叠 | chat_demo 展示 3 行状态流（非流式），非 LLM thought 真实展示 | **部分实现** | P0 |
| G9 | 社交分享 | 计划长图导出 + 微信分享 | 只有 Schema 定义，无生成逻辑 | **未实现** | P0 |

### P1 级差异

| # | 功能 | 设计目标 | 实际现状 | 差异 | 等级 |
|---|------|---------|---------|------|------|
| G10 | 模式系统 | 轻托管/全托管双模 | `ModeType` 枚举已定义，`_get_queue_threshold` 区分阈值 | **已实现** | P1 |
| G11 | 偏好锁 | 用户标记高优节点，重规划绝对保护 | `user_pinned` 字段在 Node 模型中，Phase3 保护 locked/pinned | **已实现** | P1 |
| G12 | 履约状态流 | 并行履约+进度可视+倒计时 | chat_demo 用 sleep 15 替代，cli_demo 打印 booking 状态 | **部分实现** | P1 |
| G13 | 冲突可视化 | 草稿阶段内联冲突提示 | `ItineraryNode.conflicts` 模型支持，但无前端渲染 | **模型支持前端未用** | P1 |
| G14 | 用户上报冲突 | 多选原因→自然语言→恢复方案 | 未实现 | **未实现** | P1 |
| G15 | Fallback 分层 | 覆盖 10 类失败场景 | 10 类错误码映射到 5 级 Fallback，代码完整 | **已实现** | P1 |
| G16 | 卡片完整操作 | 已完成/跳过/延后/提前 | NodeAction 枚举定义了这些操作 | **模型支持** | P1 |

### P2 级差异

| # | 功能 | 实际现状 | 差异 |
|---|------|---------|------|
| G17 | 评测体系 | 未实现 | 未实现 |
| G18 | 聚合核销看板 | 未实现 | 未实现 |
| G19 | 地图联动 | 未实现 | 未实现 |

---

## 二、架构层差异

| # | 层面 | 设计目标 | 实际现状 | 差异 |
|---|------|---------|---------|------|
| A1 | 工具层 | 11 个 API 工具，facts 模式（只返回原始数据） | ToolRegistry 11 个定义 + MockBackend 返回原始数据 | **一致** |
| A2 | 规划层 | LLM 驱动意图解析+评分+生成 | LLM 代码完整但 API 不通，退化为 mock | **代码一致，运行时异常** |
| A3 | 执行层 | 并行履约+状态同步 | Phase2 并行 booking + monitor | **一致** |
| A4 | Agent 循环 | 感知→思考→行动→观察闭环 | Orchestrator 三阶段覆盖循环，但无 LLM 的 ReAct 循环 | **部分一致** |
| A5 | 事件驱动 | EventBus 通知各层 | event_bus.py 实现 pub/sub，25+ 事件类型 | **一致** |
| A6 | 记忆层 | 三级存储 + 对话历史 | MemoryStore 三级存储存在，对话历史未传入 LLM | **部分一致** |

---

## 三、数据模型差异

| # | 模型 | 设计字段 | 实际字段 | 差异 |
|---|------|---------|---------|------|
| D1 | ItineraryData | itinerary_id/mode/scene/companions/start_time/status/nodes/feasibility/summary/duration | models.py 全部包含 | **一致** |
| D2 | ItineraryNode | node_id/poi_id/poi_name/category/resource_type/scheduled_time/duration_min/status/completed_lock/user_pinned/soft_lock/conflicts | models.py 全部包含 | **一致** |
| D3 | UserContext | scene/companions/trip_rigidity/start_time/preferences/intent_conflict/operation | models.py 含 scene/time_range/distance_constraint/missing_info/intent_conflict/mode | **基本一致**，缺少 trip_rigidity、operation |

---

## 四、测试覆盖缺口

| # | 缺失测试 | 影响 |
|---|---------|------|
| T1 | LLM 层测试（8 个全部因网络失败） | 无法验证 LLM prompt 修改的有效性 |
| T2 | 端到端集成测试 | 无法快速验证整体改动影响 |
| T3 | 异常场景测试（超时/API失败/Fallback） | Fallback 代码无运行时验证 |
| T4 | Feasibility check 阻断测试 | 新增阻断逻辑未验证 |
| T5 | POI 合法性校验测试 | 新增过滤逻辑未验证 |

---

## 五、关键数据验证

| 数据点 | 设计目标 | 实际运行值 | 来源 |
|--------|---------|-----------|------|
| 家庭活动数 | >= 8 | 8 | chat_demo 日志 |
| 家庭餐厅数 | >= 12 | 12 | chat_demo 日志 |
| 朋友活动数 | >= 4 | 4 | mocks 代码 |
| 朋友餐厅数 | >= 6 | 3 | mocks 代码 |
| 路线数据 | >= 10 对 | 4 | route_data.py |
| 城市漫步数据 | >= 2 | 2 | citywalk_data.py |

### 数据不足项
- **朋友餐厅**：设计 >= 6，实际 3
- **路线数据**：设计 >= 10 对，实际 4 对

---

## 六、差异汇总

| 类别 | 数量 | 明细 |
|------|------|------|
| **未实现** | 5 | G1 追问/G2 草稿操作/G9 社交分享/G14 用户上报冲突/G17 评测体系 |
| **部分实现** | 5 | G4 弹窗/G8 CoT/G12 履约流/A4 Agent 循环/A6 记忆对话历史 |
| **代码已加未运行验证** | 3 | G5 Feasibility 阻断/G7 POI 过滤/T4 T5 测试缺 |
| **模型支持前端未用** | 3 | G3 卡片状态/G13 冲突可视化/G16 卡片操作 |
| **运行时异常** | 1 | A2 LLM 规划 API 不通 |
| **数据量不足** | 2 | 朋友餐厅 3<6，路线 4<10 |
