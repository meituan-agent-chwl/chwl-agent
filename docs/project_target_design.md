# 项目目标设计

来源：mvp_prd_v3.md + 设计文档 + mock_api_design.md + agent_prompt_skill_design.md

---

## 一、功能列表（P0/P1/P2）

### P0（MVP 最小可行，缺少则不合格）

| 模块 | 功能 | 设计文档引用 |
|------|------|-------------|
| 意图理解 | 解析时间、人群、距离、偏好；特殊人员优先级：儿童>成人 | v3:L37 |
| 草稿操作 | 换一个/删除/插单，替换后自动对齐时间 | v3:L38 |
| 卡片状态 | 正常/风险/严重冲突三级视觉状态 | v3:L39 |
| 异常处理 | 分层重规划，知情权弹窗，绝不擅自修改 | v3:L40 |
| 行程校验 | 通勤/游玩比例 + 物理可行性，不合格禁止输出 | v3:L41 |
| 资源损失警告 | 取消有成本节点时告知损失，二次确认 | v3:L42 |
| 安全机制 | 高风险拦截+物理校验+支付不伪装+工具调用安全 | v3:L43 |
| CoT 展示 | 规划过程可视化，可展开查看推理 | v3:L44 |
| 社交分享 | 计划长图导出，微信分享 | v3:L45 |
| 双模式 | 轻托管/全托管双模 | v3:L46 |
| 偏好锁 | 用户标记高优节点，重规划绝对保护 | v3:L47 |

### P1（增强体验）

| 功能 | 描述 |
|------|------|
| 用户上报冲突 | 多选原因→自然语言→恢复方案 |
| 履约状态流 | 并行履约+进度可视化 |
| 冲突可视化 | 草稿阶段内联冲突提示 |
| Fallback 分层 | 覆盖 10 类失败场景 |
| 卡片完整操作 | 已完成/跳过/延后/提前 |

### P2（后续迭代）

| 功能 | 描述 |
|------|------|
| 评测体系 | 规则+LLM Judge+冲突模拟器 |
| 聚合核销看板 | 卡片下钻+二维码 |
| 地图联动 | 全屏大地图+导航 |

---

## 二、用户流程（5 阶段）

### Stage 1: 意图唤醒与追问
- 用户输入模糊需求 → **最多 1 轮追问**（出发时间、儿童信息）
- 提供快捷标签（2点出发/室内/怕晒）
- 不做问卷式多轮审讯

### Stage 2: CoT 规划 + 草稿画布
- **实时流式展示**思考过程，默认折叠
- 草稿阶段无真实预订，用户完全主权
- 支持：替换/删除/插入/延后/提前/跳过/Pin/完成打卡/问题上报
- 同一节点替换上限 5 次

### Stage 3: 并行履约 + 进度流水线
- 所有节点并行发起，按时间序展示
- 状态：待履约→履约中→成功→降级

### Stage 4: 出行看板
- 天气提示 + 全程预估
- 社交分享（P0）
- 地图模式 / 核销看板（P2）

### Stage 5: 异常感知 + 重规划
- 排队暴增 / 天气骤变 / 用户疲劳 → 局部重规划
- 知情权弹窗：用户确认后才执行
- 保护 completed_lock + user_pinned 节点

---

## 三、工具层设计

### 11 个 API 工具

| # | 工具 | 类型 | 调用方式 |
|---|------|------|---------|
| 1 | location/current | 事实查询 | 并行 |
| 2 | user/context | 意图解析 | 串行（最先） |
| 3 | weather | 事实查询 | 并行 |
| 4 | activities/search | 事实查询 | 并行 |
| 5 | restaurants/search | 事实查询 | 并行 |
| 6 | route/check | 路线计算 | 后置（候选确定后） |
| 7 | candidates/score | LLM 评分 | 后置 |
| 8 | itinerary/generate | 行程生成 | 最后 |
| 9 | booking/execute | 履约执行 | 并行（确认后） |
| 10 | booking/status | 履约查询 | 轮询 |
| 11 | itinerary/replan | 重规划 | 异常触发 |

### 调用策略
- **并行批次 1**：location + weather + activities + restaurants
- **串行**：candidates_score → route_check → itinerary_generate
- **并行批次 2**：booking_execute（所有节点同时）
- **轮询**：booking_status（独立监控协程）
- **异常触发**：itinerary_replan

### 数据边界
- 工具层只返回原始事实数据，不返回判断（"推荐""高风险"）
- Planner 层根据事实做推理

---

## 四、规划层设计

### 意图解析器
- 输入：用户自然语言 / 草稿操作
- 输出：scene / companions / time / preferences / operation
- 冲突检测：intent_conflict flag

### POI 评分（6 维度）
| 维度 | 家庭权重 | 朋友权重 |
|------|---------|---------|
| 距离便利性 | HIGH | NORMAL |
| 排队成本 | HIGH | NORMAL |
| 偏好匹配 | NORMAL | NORMAL |
| 节奏舒适度 | HIGH | NORMAL |
| 现实可执行性 | NORMAL | NORMAL |
| 热门/价格 | NORMAL | HIGH |

### 模式差异化
- **轻托管**：排队 >45min 扣分，氛围感 ×1.3
- **全托管**：排队 >30min -30 分，child_friendly ×2.0

### 6 条合理性校验规则
1. 通勤单程 ≤30%，往返 ≤40%
2. 主活动 ≥45min，含儿童 ≥60min
3. 无车 >15km 禁止，步行 >2km 禁止，含儿童 >800m 标注风险
4. 根本冲突时先确认意图
5. 节点 ≤6，含儿童 20:00 前结束
6. 极端天气禁止户外

### 行程生成
- 固定结构：主活动 → 餐厅 → 可选轻活动
- 活动→餐厅之间留 10-15min 交通缓冲

---

## 五、Agent 循环设计

### 闭环流程
```
用户输入 → 工具并行取事实 → LLM 评分 → 合理性校验
→ 行程生成 → 用户编辑循环 → 用户确认
→ 并行履约 → 后台 watch → 异常触发 → 重规划 → 用户确认
```

### 事件驱动
- EventBus 发布 25+ 种事件类型
- Background Watch 四维监控：排队/天气/预约/余量
- 重规划保护原则：只改 pending 节点，保护 locked/pinned

### 触发条件
| 异常 | 阈值 |
|------|------|
| 排队暴增 | >30min(全托管)/45min(轻托管) |
| 天气骤变 | heavy_rain/storm |
| 用户疲劳 | "太累了"/"孩子困了" |

---

## 六、记忆层设计

### 三级存储

| 层级 | 内容 | 来源 | 生命周期 |
|------|------|------|---------|
| session_facts | 本次会话事实（孩子5岁、14:00出发） | 用户输入/追问 | 会话结束可丢弃 |
| confirmed_preferences | 长期偏好（口味清淡、倾向打车） | 用户授权确认 | 跨会话持久化 |
| derived_preferences | 临时倾向（可能喜欢室内） | 事件提取 | 低置信度，需确认后提升 |

### 写入规则
- 只能由用户输入 / 前端询问 / 用户确认 写入
- 不能预置用户画像
- 长期偏好必须用户显式授权

### 支持操作
- put / get / delete / clear
- promote（derived → confirmed 置信度升级）
- TTL 过期自动清理
- 磁盘持久化

---

## 七、数据模型

### Itinerary 顶层
- itinerary_id / mode / scene / companions
- start_time / status / nodes[]
- feasibility_check（commute_ratio, passed）
- summary / total_duration_min

### Node 节点
- node_id / poi_id / poi_name / category / resource_type
- scheduled_time / duration_min
- status / completed_lock / user_pinned / soft_lock
- conflicts[]（type, severity, message）

### 前端 Schema（6 组）
1. ItineraryCardSchema — 行程卡片
2. StatusStreamSchema — 状态流
3. RiskModalSchema — 风险弹窗
4. AlternativeNodesSchema — 备选节点
5. FulfillmentStatusSchema — 履约状态
6. ShareMessageSchema — 分享消息

---

## 八、异常处理

### 5 级 Fallback
| 级别 | 策略 |
|------|------|
| L0-L1 | Retry / 同类替换 |
| L2 | 同商圈替换 |
| L3 | 删减非关键节点 |
| L4 | 重规划剩余 |
| L5 | 人工处理 |

### 10 类失败场景
AI 生成失败 → POI 匹配失败 → 餐厅不可用 → 预约失败
→ 执行超时 → 用户变更 → 软锁取消 → 定位失败
→ 全局崩溃 → Mock 耗尽

### 结构化输出降级
- 第 1 次：告知错误要求修复
- 第 2 次：强制恢复结构
- 第 3 次：降级模板 `{"status":"fallback"}`
