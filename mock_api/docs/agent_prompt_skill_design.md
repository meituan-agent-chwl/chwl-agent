# Agent 功能与 Prompt/Skill 设计

## 1. Agent 定位

本赛题中的 Agent 不是普通推荐助手，而是本地生活管家型执行 Agent。它的目标是帮助用户把一个模糊的下午出行目标，变成可执行、可履约、可调整的完整方案。

核心边界：

- 记忆来自用户输入、前端询问、用户确认或事件提取，不能凭空预置。
- 事实来自 Mock API 或真实 API，Agent 不能编造排队、天气、预约状态。
- 决策来自 Planner，但涉及支付、取消、替换已预约节点等有成本动作，必须用户确认。
- 给前端展示的内容必须结构化，不能依赖大段自然语言。

## 2. Agent 应具备的核心功能

### 2.1 专属管家身份

Agent 需要有稳定身份：省心、低打扰、现实可执行、可随时调整。它不是“推荐官”，而是“帮你把下午安排好并持续盯风险的人”。

### 2.2 偏好理解与记忆写入

Agent 从用户输入中提取本次任务事实和偏好，例如：

- 家庭场景或朋友场景
- 出发时间与出发地
- 同行人信息
- 孩子年龄
- 饮食偏好
- 不想太远
- 不想排队
- 轻松模式、探索模式或托管模式

这些内容写入记忆仓库时应区分：

- `session_facts`：本次会话事实，如“孩子 5 岁”“今天 14:00 出发”。
- `confirmed_preferences`：用户明确授权保存的长期偏好。
- `derived_preferences`：Agent 从事件中提取的临时倾向，需低置信处理。

### 2.3 API 调用与事实聚合

Agent 调用 Mock API 获取结构化事实：

- 活动候选
- 餐厅候选
- 路线耗时
- 天气状态
- 排队状态
- 预约状态
- 履约入口
- 备用节点

Agent 不应凭空补充事实数据。

### 2.4 Planning 与可执行性分析

Agent 将事实组合成 4-6 小时方案，并校验：

- 距离是否过远
- 通勤占比是否过高
- 餐厅是否排队太久
- 是否适合孩子
- 是否适合减脂饮食
- 是否受天气影响
- 是否需要预约
- 时间是否能接上

### 2.5 结构化前端输出

前端需要地点卡片、状态流、风险弹窗、备用节点和履约状态，因此 Agent 输出必须遵循 JSON Schema。

### 2.6 履约协同

Agent 不做真实支付，也不选择套餐。它只负责：

- 打开购票页、预约页、取号页、地图页
- 记录用户已完成、失败或跳过
- 生成 Mock 核销码
- 锁定已完成节点

### 2.7 后台监控

用户确认方案后，Agent 创建后台 watch：

- 盯餐厅排队
- 盯天气
- 盯预约状态
- 盯活动余量

这体现 7x24 自主协同，而不是一次性问答。

### 2.8 异常处理与局部重规划

遇到排队暴增、天气转暴雨、预约售罄、孩子累了、用户临时改主意时：

- 识别受影响节点
- 保护已完成、已预约、用户锁定节点
- 查询备用节点
- 返回 2 个推荐 + 更多候选
- 等用户确认后再替换

## 3. 建议 Prompt / Skill 清单

### 3.1 Butler Identity Prompt

用途：定义专属管家身份和行为边界。

核心约束：

```text
你是本地生活管家 Agent。
目标是降低用户决策成本，保证现实可执行。
不要只推荐，要推进规划、履约和异常恢复。
涉及支付、取消、替换已预约节点，必须用户确认。
不要伪造 API 事实。
不要输出前端无法解析的内容。
```

### 3.2 Preference Extraction Skill

用途：从用户自然语言中提取任务事实和偏好。

输出示例：

```json
{
  "session_facts": {
    "scenario": "family",
    "start_time": "14:00",
    "companions": ["spouse", "child"],
    "child_age": 5,
    "home_area": "北京望京"
  },
  "preferences": {
    "distance": "nearby",
    "food": ["清淡", "低油", "减脂"],
    "mode": "light_managed"
  },
  "need_clarification": false,
  "clarifying_question": null
}
```

### 3.3 Memory Writer Skill

用途：把用户确认的信息写入记忆仓库。

原则：

- 本轮任务事实写入 `session_facts`。
- 长期偏好必须用户授权后写入 `confirmed_preferences`。
- 事件中提取出的偏好写入 `derived_preferences`，不能当成长期事实。

### 3.4 Fact Fetch Skill

用途：根据任务类型决定调用哪些 API。

家庭场景常用：

```text
activities/search
restaurants/search
weather/current
route/estimate
booking/status
queue/status
```

朋友场景常用：

```text
activities/search
citywalk/detail
restaurants/search
weather/current
route/estimate
booking/status
```

### 3.5 Itinerary Planner Skill

用途：生成行程卡片。

输出示例：

```json
{
  "plan_id": "plan_001",
  "summary": "下午2点出发，先去室内亲子乐园，再吃清淡晚饭。",
  "nodes": [
    {
      "node_id": "node_001",
      "type": "activity",
      "poi_id": "act_family_001",
      "title": "汤姆猫亲子乐园",
      "start_time": "14:20",
      "end_time": "15:50",
      "status": "draft",
      "tags": ["室内", "亲子友好", "雨天友好"],
      "actions": ["replace", "delete", "pin", "open_booking"]
    }
  ],
  "feasibility": {
    "commute_ratio_ok": true,
    "child_safety_ok": true,
    "weather_ok": true,
    "queue_ok": true
  }
}
```

### 3.6 Feasibility Checker Skill

用途：防止看起来合理但实际翻车。

校验项：

- 通勤占比
- 儿童步行距离
- 排队阈值
- 营业时间
- 预约状态
- 天气风险
- 结束时间
- 节点数量

输出示例：

```json
{
  "pass": true,
  "risk_flags": [],
  "explanation": "总通勤约35分钟，占比合理；活动和餐厅均可履约。"
}
```

### 3.7 Frontend JSON Formatter Skill

用途：保证前端稳定渲染。

需要覆盖的 Schema：

- `itinerary_card_schema`
- `status_stream_schema`
- `risk_modal_schema`
- `alternative_nodes_schema`
- `fulfillment_status_schema`
- `share_message_schema`

### 3.8 Fulfillment Coordinator Skill

用途：推进履约入口和用户确认状态。

允许：

- 打开购票页
- 打开预约页
- 打开取号页
- 打开地图页
- 记录用户确认结果

禁止：

- 真实付款
- 替用户选择套餐
- 伪造支付成功

### 3.9 Background Watch Skill

用途：用户确认方案后创建后台监控任务。

输出示例：

```json
{
  "watch_targets": [
    {
      "type": "queue",
      "poi_id": "rest_family_001",
      "threshold": {
        "estimated_wait_min_gt": 35
      }
    },
    {
      "type": "weather",
      "location": "北京朝阳区",
      "threshold": {
        "rain_level_in": ["heavy"]
      }
    }
  ]
}
```

### 3.10 Replanning Skill

用途：异常恢复与局部重规划。

输出示例：

```json
{
  "event_type": "queue_spike",
  "affected_nodes": ["node_002"],
  "protected_nodes": ["node_001"],
  "recommended_replacements": [
    {
      "poi_id": "rest_family_002",
      "reason": "同商圈，排队12分钟，清淡且有儿童椅"
    }
  ],
  "requires_user_confirmation": true
}
```

### 3.11 LLM Environment Simulator Skill

用途：模拟动态环境事件，让 Mock API 更贴近真实世界。

边界：

- 只生成事实事件
- 不做最终行程决策
- 不替用户确认支付、取消或替换

### 3.12 Fallback Repair Skill

用途：当模型输出不符合结构化要求时修复 JSON，或降级到规则模板。

## 4. 防止 Agent 不结构化输出

### 4.1 Prompt 强约束

```text
你必须只输出 JSON。
禁止 Markdown。
禁止解释性前缀。
禁止多余文本。
字段缺失时填 null。
```

### 4.2 JSON Schema 校验

输出后进行校验：

- 能否 parse JSON
- 字段是否齐全
- 类型是否正确
- 枚举值是否合法
- 是否包含前端无法识别的字段

### 4.3 失败重试

```text
第1次失败：把 schema 错误返回给模型，要求修复 JSON。
第2次失败：再次要求只修复结构，不改业务内容。
第3次失败：使用规则模板生成最小可用结果。
```

### 4.4 降级结果示例

```json
{
  "status": "fallback",
  "message": "方案结构生成失败，已切换到低风险模板。",
  "nodes": []
}
```

## 5. 推荐最小实现

MVP 至少包含：

```text
1. butler_identity
2. preference_extraction
3. memory_writer
4. fact_fetcher
5. itinerary_planner
6. feasibility_checker
7. fulfillment_coordinator
8. background_watch_and_replanner
```

增强版可加入：

```text
9. frontend_json_formatter
10. llm_environment_simulator
11. fallback_repair
12. share_message_generator
```

## 6. 总体链路

```text
用户输入
→ 专属管家理解偏好
→ 写入会话记忆
→ 调用 Mock API 获取事实
→ 规划可执行行程
→ JSON 输出给前端
→ 用户确认
→ 打开履约入口
→ 后台 watch
→ 事件触发
→ 局部重规划
→ 用户确认替换
```

核心原则：记忆来自用户，事实来自 API，决策来自 Planner，展示必须结构化，有成本动作必须确认。

