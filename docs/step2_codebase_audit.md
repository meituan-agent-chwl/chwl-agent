# 第二步：代码库现状检查

对照来源：step1 设计目标 vs 实际代码 + 运行结果
运行证据：102 pytest passed (用户 2026-06-05 运行) / chat_demo 输出 (LLM 连接失败) / test_llm_planner 8 failed

---

## 一、已经完成的功能

### 1.1 状态机引擎
| 组件 | 证据 |
|------|------|
| Itinerary 级 7 态 FSM | `core/state_machine.py:20-28` 定义了 7 种状态 |
| Node 级 10 态 FSM | `core/state_machine.py:31-44` 定义了 10 种状态 |
| 9 条行程状态转换 | `core/state_machine.py:166-187` create_itinerary_fsm |
| 18 条节点状态转换 | `core/state_machine.py:189-230` create_node_fsm |
| Guard 守卫条件 | `core/state_machine.py:53` Transition.guard |
| 状态变更事件通知 | `core/state_machine.py:153-160` on_change / _emit |
| **验证** | test_state_machine.py 17 tests 全部通过 |

### 1.2 工具注册与调用
| 组件 | 证据 |
|------|------|
| 11 个工具定义注册 | `core/tool_registry.py:106-125` _register_defaults |
| 并行调用 invoke_parallel | `core/tool_registry.py:281-300` |
| 指数退避重试 | `core/tool_registry.py:203-215` retry loop |
| 熔断器 | `core/tool_registry.py:149-173` circuit breaker |
| mock handler 注册 | `core/tool_registry.py:127-132` register_mock |
| HTTP 模式切换 | `core/tool_registry.py:141` set_base_url |
| **验证** | test_tool_registry.py 6 tests 全部通过 |

### 1.3 数据模型
| 组件 | 证据 |
|------|------|
| ItineraryData | `core/models.py:129-186` 完整字段 |
| ItineraryNode | `core/models.py:87-127` 完整字段含 conflicts/score |
| UserContext | `core/models.py:189-200` 含 intent_conflict |
| FeasibilityCheck | `core/models.py:70-76` commute_ratio/passed |
| 6 种枚举 | `core/models.py:18-58` SceneType/CompanionType/NodeCategory/ResourceType/ModeType |
| **验证** | test_orchestrator.py 12 tests 全部通过 |

### 1.4 三级记忆仓库
| 组件 | 证据 |
|------|------|
| session_facts 支持 | `core/memory_store.py:96-117` put/put_batch |
| confirmed_preferences 支持 | 同 put 方法，tier 参数控制 |
| derived_preferences 支持 | 同 put 方法，tier 参数控制 |
| promote 机制 | `core/memory_store.py:177-196` promote |
| TTL 过期 | `core/memory_store.py:42-46` is_expired |
| 磁盘持久化 | `core/memory_store.py:292-313` save/load |
| **验证** | test_memory_store.py 19 tests 全部通过 |

### 1.5 前端 6 组 JSON Schema
| 组件 | 证据 |
|------|------|
| ItineraryCardSchema | `schemas/__init__.py:49-91` |
| StatusStreamSchema | `schemas/__init__.py:143-153` |
| RiskModalSchema | `schemas/__init__.py:170-196` |
| AlternativeNodesSchema | `schemas/__init__.py:215-235` |
| FulfillmentStatusSchema | `schemas/__init__.py:251-265` |
| ShareMessageSchema | `schemas/__init__.py:275-291` |
| Schema 注册表 | `schemas/__init__.py:292-311` ALL_SCHEMAS + SCHEMA_TYPE_MAP |

### 1.6 Orchestrator 三层流程
| 组件 | 证据 |
|------|------|
| Phase 1 规划 (5 路并行) | `orchestrator/orchestrator.py:320-499` _phase1_plan |
| Phase 2 履约 (并行 booking) | `orchestrator/orchestrator.py:516-570` _phase2_execute |
| Phase 3 重规划 (异常处理) | `orchestrator/orchestrator.py:700-760` _phase3_replan |
| 5 级 Fallback | `orchestrator/orchestrator.py:841-931` _handle_node_failure |
| EventBus 事件发布 | `orchestrator/event_bus.py:55-83` emit |
| ConfirmationGateway | `orchestrator/confirmation_gateway.py:38-145` |
| **验证** | test_orchestrator.py 12 tests 全部通过 |

### 1.7 后台监控
| 组件 | 证据 |
|------|------|
| 排队监控 | `orchestrator/background_watch.py:236-260` _check_queue |
| 天气监控 | `orchestrator/background_watch.py:262-286` _check_weather |
| 预约监控 | `orchestrator/background_watch.py:288-314` _check_booking |
| 余量监控 | `orchestrator/background_watch.py:316-344` _check_availability |
| watch 告警事件 | `orchestrator/background_watch.py:346-371` _trigger_alert |
| **验证** | test_background_watch.py 9 tests 全部通过 |

### 1.8 JSON 修复与校验
| 组件 | 证据 |
|------|------|
| 5 种 JSON 修复策略 | `core/fallback_repair.py:37-63` repair |
| OutputValidator 校验 | `core/output_validator.py:78-108` validate |
| 3 次重试 + 降级 | `core/output_validator.py:175-196` track_retry/get_fallback |
| **验证** | test_output_validator.py 25 tests 全部通过 |

### 1.9 环境模拟器
| 组件 | 证据 |
|------|------|
| 预设家庭场景时间线 | `mocks/env_simulator.py:100-110` schedule_preset_family_scenario |
| 预设朋友场景时间线 | `mocks/env_simulator.py:112-123` schedule_preset_friends_scenario |
| 预设自动演示时间线 | `mocks/env_simulator.py:125-144` schedule_preset_auto_demo |
| 排队暴增模拟 | `mocks/env_simulator.py:199-218` _handle_queue_spike |
| 天气变化模拟 | `mocks/env_simulator.py:220-234` _handle_weather_change |
| **验证** | test_env_simulator.py 9 tests 全部通过 |

### 1.10 Mock 数据（已扩展到 8+12）
| 组件 | 证据 |
|------|------|
| 8 个家庭活动 | `mocks/__init__.py:46-160` MOCK_ACTIVITIES_FAMILY |
| 12 个家庭餐厅 | `mocks/__init__.py:222-392` MOCK_RESTAURANTS_FAMILY |
| 4 个朋友活动 | `mocks/__init__.py:162-220` MOCK_ACTIVITIES_FRIENDS |
| **验证** | chat_demo 运行日志显示"已找到 8 个活动和 12 个餐厅" |

---

## 二、未完全达到预期的功能

### 2.1 LLM 驱动规划（有代码但运行时 LLM 连接失败）
| 预期 | 实际 | 证据 |
|------|------|------|
| LLM 理解用户意图 | `core/llm_planner.py:186-215` handle_user_context 已实现 | 运行时错误："All connection attempts failed" |
| LLM 评分 POI | `core/llm_planner.py:217-248` handle_candidates_score 已实现 | 运行时错误，fallback 到 hash 评分 |
| LLM 生成行程 | `core/llm_planner.py:250-296` handle_itinerary_generate 已实现 | 运行时错误，LLM 不可用时无备选 |
| LLM 重规划 | `core/llm_planner.py:298-323` handle_itinerary_replan 已实现 | 未验证（依赖 LLM） |

**结论**：LLM planner 代码完整，但实际运行时无法连接 DeepSeek API，导致退化为 mock 模式。

### 2.2 前端 Schema 已定义但未使用
| 预期 | 实际 | 证据 |
|------|------|------|
| 前端按 Schema 渲染 | Schema 定义在 `schemas/__init__.py` | OutputValidator 支持校验但 chat_demo 和 cli_demo 未使用 schema 输出 |
| **验证** | 无可运行验证 | 需前端接入确认 |

### 2.3 MemoryStore 已集成到 Orchestrator 但对话历史未传入 LLM
| 预期 | 实际 | 证据 |
|------|------|------|
| 三级记忆存入 | `orchestrator/orchestrator.py:338-356` Phase1 中写入 session_facts | 已验证，写入了 session_facts |
| 对话历史传给 LLM | `chat_demo.py` conversation_history 存在但未在 handle_message 中传给 LLM | 多轮对话无上下文 |

---

## 三、和设计不符合的功能

### 3.1 追问环节缺失
| 设计要求 | 实际实现 | 证据 |
|---------|---------|------|
| 阶段一"结构化轻量追问最多 1 轮" | chat_demo 直接进入规划，无追问 | `chat_demo.py:52-55` start() 直接打招呼等输入，无追问逻辑 |
| 提供快捷标签（2点出发/室内/怕晒） | 无对应功能 | 全局搜索无"标签"相关选择 UI |

### 3.2 草稿态用户操作未暴露
| 设计要求 | 实际实现 | 证据 |
|---------|---------|------|
| 替换/删除/插入/Pin/延后/跳过 操作 | `core/models.py:209-217` ItineraryModification 定义了这些操作 | chat_demo 和 cli_demo 中没有暴露这些能力给用户 |

### 3.3 LLM 编造 POI（已加过滤但未运行验证）
| 设计要求 | 实际实现 | 证据 |
|---------|---------|------|
| "禁止 LLM 编造不在 Mock 池中的 POI" | `core/llm_planner.py:268-280` 加了 POI ID 合法性校验 | 代码层面已加过滤（`validate_nodes`），但 LLM 不可用，无法运行验证 |
| **运行证据** | 之前的 chat_demo 输出 "乐高探索中心餐厅" 为不存在的 POI | 用户 2026-06-05 运行输出 |

### 3.4 Feasibility check 不通过未阻断输出（已修复未验证）
| 设计要求 | 实际实现 | 证据 |
|---------|---------|------|
| "任何一条不通过则方案不得输出" | `orchestrator/orchestrator.py:482-496` 加了检查 + raise | 代码已加，但未运行验证 |
| 修复前：只检查不阻断 | 同一文件旧版本：`_validate_feasibility` 调用后不检查 `fc.passed` | 审计报告指出 |

### 3.5 履约进度实时展示不全
| 设计要求 | 实际实现 | 证据 |
|---------|---------|------|
| 并行履约，按时间序展示逐条状态 | `chat_demo.py:269-283` _do_execute 用 await asyncio.sleep(15) 替代实时展示 | 代码行 276 |

### 3.6 资源损失警告未展示给用户
| 设计要求 | 实际实现 | 证据 |
|---------|---------|------|
| 取消有成本节点时弹窗告知损失 | `orchestrator/orchestrator.py:273-308` cancel_session 发出 resource_loss_warning 事件 | chat_demo 未展示给用户 |
| 二次确认后才执行 | 无对应 UI/交互 | 用户看不到损失信息 |

### 3.7 社交分享未实现
| 设计要求 | 实际实现 | 证据 |
|---------|---------|------|
| 计划长图导出 + 微信分享 | Schema 已定义 (`schemas/__init__.py:275-291`) | 无生成逻辑，无导出代码 |

---

## 四、测试覆盖缺口

| 缺失测试 | 证据 |
|---------|------|
| LLM 层测试 | test_llm_planner.py 8 个测试全部因 LLM 连接失败而失败 |
| 端到端集成测试 | 无测试从 start_session → confirm → verify completed 的完整链路 |
| 异常场景测试 | 无测试覆盖网络超时、API 失败时的 Fallback 行为 |

---

## 五、运行验证结果摘要

| 验证项 | 结果 | 证据来源 |
|--------|------|---------|
| 核心测试 102 个 | ✅ 全部通过 | 用户终端输出 2026-06-05 |
| Fact 数据搜索 | ✅ 8 活动 + 12 餐厅 | chat_demo 日志 |
| LLM 意图理解 | ❌ 连接失败 | test_llm_planner 8 fails |
| LLM POI 评分 | ❌ 连接失败 | test_llm_planner 8 fails |
| LLM 行程生成 | ❌ 连接失败 | test_llm_planner 8 fails |
| 内存 Mock 模式 | ✅ 可运行 | cli_demo.py 正常运行 |
