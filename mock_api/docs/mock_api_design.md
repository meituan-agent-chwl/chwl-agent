# Mock API 设计文档

## 1. 定位与边界

本 Mock API 是本地生活 Agent Demo 的“动态事实沙盒”，用于模拟美团、地图、天气、排队、预约入口和后台监控任务。它不承担用户意图解析、方案评分、可行性判断、节点排序和解释生成；这些属于 Agent/Planner 层。

这样拆分的原因是：赛题痛点不是“再写一个推荐接口”，而是解决用户在点评、地图、打车、天气、公众号之间反复切换的问题。Mock API 负责把分散事实统一成结构化数据，Agent 基于这些事实完成规划、履约推进和异常恢复。

代码层面也按仓库边界拆分：`mock_data/` 存放外部世界事实数据，`memory_store/` 单独存放会话记忆。记忆初始为空，只能由前端询问结果或 Agent 从用户事件中提取出的显式信息写入。

数据字段也遵循事实边界：Mock 数据不写“亲子友好、雨天友好、适合减脂、低决策成本、距离较远”等判断性标签，而是写客观属性，例如 `venue.environment`、`facilities.child_seat`、`menu_features`、`age_policy`、`booking_required`、`available_slots`。是否适合用户，由 Agent 根据用户偏好和这些事实推导。

## 2. API 分层

### 事实查询层

- `GET /api/memory/profile`：返回当前会话中已被前端或 Agent 写入的记忆。初始为空，不预置用户画像。
- `GET /api/activities/search`：查询亲子乐园、绘本馆、儿童科技馆、国博、Citywalk、桌游馆、展览、LiveHouse 等候选。
- `GET /api/citywalk/detail`：把 Citywalk 拆成具体 stop，适配地点卡片 UI。
- `GET /api/restaurants/search`：查询餐厅候选，返回排队、菜系、人均、儿童椅、轻食等事实。
- `GET /api/route/estimate`：模拟高德类路径接口，只返回距离、耗时、步行距离和交通状态。
- `GET /api/weather/current`：返回当前天气事实。
- `GET /api/queue/status`：返回实时排队。
- `GET /api/booking/status`：返回预约、余票、外部入口。

### 履约入口层

- `POST /api/fulfillment/open-link`：返回购票页、公众号预约页、取号页或地图页 Mock URL。
- `POST /api/fulfillment/confirm-user-action`：用户完成外部购买/预约后回填结果。
- `GET /api/fulfillment/status`：查询已确认履约状态和核销码。

Demo 不模拟真实支付，不替用户选择套餐，也不伪造付款成功。涉及成本的动作由用户在外部入口完成后显式确认。

### 后台协同层

- `POST /api/watch/create`：创建后台 watch 任务，模拟 Agent 持续盯餐厅排队、天气、预约余量。
- `GET /api/watch/status`：查询 watch 状态。
- `GET /api/events/poll`：拉取动态事件，例如排队暴增、天气转暴雨、预约售罄。

这层用于体现 7x24 自主协同：Agent 不只是问答，而是在用户确认方案后继续关注会导致翻车的事实变量。

### 备用恢复层

- `GET /api/alternatives/search`：根据受影响节点和原因返回 2 个推荐备用节点与更多候选。
- `GET /api/sandbox/scenario-script`：返回给大模型事件模拟器使用的时间段、事件类型和安全边界。
- `POST /api/sandbox/trigger-event`：演示时手动触发事件，保证现场可控。
- `POST /api/sandbox/apply-llm-event`：接收大模型生成的环境事件，并将其应用到天气、排队或预约状态。
- `POST /api/sandbox/reset`：重置沙盒状态。

备用节点 API 不直接替换方案，只提供事实候选。是否替换、替换哪一段、是否保护已预约节点，由 Agent 决定并让用户确认。

## 3. 大模型驱动的数据模拟

为了让动态沙盒更贴近真实世界，Mock API 预留了 LLM Event Simulator Hook。大模型根据场景脚本生成“环境事实变化”，例如：

- 17:30 晚餐高峰导致餐厅排队从 18 分钟升至 65 分钟。
- 下午雷阵雨增强，户外 Citywalk 风险升高。
- 热门场馆预约余量变为已满。

这里的大模型只扮演“现实环境模拟器”，不扮演 Planner。它输出结构化事件，Mock API 通过 `/api/sandbox/apply-llm-event` 更新事实状态，Agent 再调用 `events/poll`、`queue/status`、`weather/current`、`alternatives/search` 完成提醒和恢复。

这样既利用了大模型生成复杂环境变化的能力，也保持了职责边界：事实模拟归沙盒，决策解释归 Agent。

## 4. 设计考虑

### 对齐用户痛点

- 减少 App 切换：统一模拟活动、餐厅、天气、地图、公众号预约入口。
- 降低决策疲劳：所有 POI 返回可比较字段，如距离、排队、预约、风险事实、外部入口。
- 避免规划翻车：动态事件能模拟排队暴增、暴雨、预约售罄。
- 从检索到执行：履约入口和用户确认状态让 Demo 展示“帮你推进事情”，而不是只给推荐。

### 对齐赛题要求

- 专属管家：`memory/profile` 只保存用户显式确认或事件中提取出的偏好，不凭空预置画像。
- 至少 3 个本地生活技能：路径规划、餐厅排队监控、天气活动抓取都能调用本 API。
- 7x24 后台协同：`watch/create` 与 `events/poll` 展示后台持续任务。
- 动态模拟沙盒：`sandbox/trigger-event` 修改真实 API 返回状态，而不是只展示静态 JSON。

## 5. 场景数据

家庭场景覆盖：汤姆猫亲子乐园、Bingo Bay、绘本馆、中国电影博物馆、国家自然博物馆；餐厅覆盖轻食、清淡粤菜、亲子友好餐厅。异常包括餐厅排队从 18 分钟升至 70 分钟、孩子累了。孩子年龄、老婆减脂等信息应由前端询问或用户输入后写入记忆，而不是 Mock API 初始自带。

朋友场景覆盖：中国国家博物馆、朝阳公园 Citywalk、国子监街-五道营胡同 Citywalk、桌游馆、今日美术馆、蓝色港湾商场展览、MAO LiveHouse。异常包括天气转暴雨、国博预约售罄。

地点名称尽量选用北京实际存在或接近真实业务生态的地点；排队、余票、路线耗时和事件状态为 Demo Mock 数据。

## 6. 与 Agent 的职责边界

Mock API 提供事实：

- 什么地点存在
- 离家多远
- 当前排队多久
- 是否需要预约
- 入口在哪里
- 天气是否变化
- 有哪些可替代地点

Agent/Planner 负责决策：

- 是否适合孩子、朋友、减脂需求
- 通勤占比是否过高
- 是否应该替换节点
- 哪些已锁定节点不能动
- 如何向用户解释风险和备选方案
