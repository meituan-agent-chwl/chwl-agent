# CLAUDE.md — Web Agent 架构约束文档

> 本文件是项目唯一架构权威。每次修改任何文件前，必须重新阅读相关章节。

---

## 一、当前状态（已损坏，必须修复）

系统存在控制权归属错误，不是功能缺失：

- `api/app.py` 直接调用 `orchestrator.start_session()` 生成结果 ← 必须删除
- `ChatAgent` 未成为 session 执行主体 ← 必须修复
- `EventBus` 没有 session scope ← 必须修复
- SSE 被当作输出层而非 runtime projection ← 必须修复
- LLM routing 未进入主链路 ← 必须修复

**核心问题本质：系统没有 "session-owned agent runtime"，只有 "一次性 orchestration execution"**

---

## 二、目标架构（必须严格对齐）

```
Frontend
   ↓  HTTP POST /chat
api/app.py  (Router Layer — 只做路由，禁止业务逻辑)
   ↓
session_manager.py → get_or_create_agent(session_id)
   ↓
ChatAgent.handle_message(user_message)  (唯一执行主体)
   ↓
EventBus.emit(event, session_id=session_id)  (session-scoped)
   ↓
sse_adapter.py  (EventBus → SSE projection)
   ↓
Frontend  (SSE stream)
```

---

## 三、核心架构约束（每次写代码前检查）

### 约束 1：ChatAgent 是唯一执行入口

```python
# ✅ 唯一允许的调用方式
agent = get_or_create_agent(session_id)
result = agent.handle_message(user_message)

# ❌ 以下调用方式全部禁止，出现即删除
orchestrator.start_session(...)
orchestrator.run(...)
planner.generate(...)
```

### 约束 2：session_id → ChatAgent 必须一对一绑定

- `agent_instances: Dict[str, ChatAgent]` 必须存在于 `session_manager.py`
- 同一 `session_id` 永远复用同一个 `ChatAgent` 实例
- **禁止在每次 request 中 `new ChatAgent()`**
- `ChatAgent` 内部必须持有：`conversation_history`、`session_id`、LLM context

### 约束 3：ChatAgent 使用 stateless continuation 模型

```python
# ✅ 允许的状态持久化方式
self.conversation_history.append(message)   # 对话历史累积
self.session_memory_buffer.update(context)   # session 内存缓冲

# ❌ 禁止
suspend / resume runtime
外部保存 execution pointer
每次请求重置 conversation_history
```

### 约束 4：EventBus 必须 session-scoped

```python
# ✅ 所有 emit 必须带 session_id
event_bus.emit("plan_complete", data=plan, session_id=session_id)

# ✅ SSE 层必须过滤
if event.session_id != request.session_id:
    continue  # 丢弃，不推送

# ❌ 全局广播禁止
event_bus.emit("plan_complete", data=plan)  # 没有 session_id → 禁止
```

---

## 四、需要修改的文件（必须按顺序处理）

### Step 1 — 读取现状（不写代码，先读）

```
读取以下文件，理解当前实现：
1. api/app.py          — 找到所有 orchestrator 调用
2. chat_agent.py       — 确认 handle_message() 签名和 conversation_history 实现
3. event_bus.py        — 确认当前是否有 session_id 过滤
4. 任何现有的 session 管理代码
```

### Step 2 — 新建 `session_manager.py`

位置：项目根目录或 `core/session_manager.py`（与现有模块结构一致）

必须实现：

```python
from typing import Dict
from chat_agent import ChatAgent

agent_instances: Dict[str, ChatAgent] = {}

def get_or_create_agent(session_id: str) -> ChatAgent:
    if session_id not in agent_instances:
        agent_instances[session_id] = ChatAgent(session_id=session_id)
    return agent_instances[session_id]

def destroy_agent(session_id: str) -> None:
    if session_id in agent_instances:
        del agent_instances[session_id]

def list_active_sessions() -> list:
    return list(agent_instances.keys())
```

### Step 3 — 修改 `chat_agent.py`

不改核心逻辑，只确保以下条件满足：

```python
class ChatAgent:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.conversation_history = []   # 必须跨 request 保持，不能每次重置
        # ... 其他初始化

    def handle_message(self, user_message: str) -> dict:
        # 必须：将 user_message 追加到 conversation_history
        # 必须：emit 的所有事件携带 self.session_id
        # 必须：支持被重复调用（幂等初始化，非幂等执行）
        pass
```

如果 `handle_message` 当前不支持重复调用，修复它，**不得改变其核心推理逻辑**。

### Step 4 — 重写 `api/app.py` 的 `/chat` 端点

删除所有 orchestrator 调用，替换为：

```python
from session_manager import get_or_create_agent
from sse_adapter import create_sse_stream

@app.route("/chat", methods=["POST"])
def chat():
    session_id = request.json.get("session_id")
    user_message = request.json.get("message")

    # Step A: 获取或创建 agent（唯一允许的逻辑）
    agent = get_or_create_agent(session_id)

    # Step B: 交给 ChatAgent 执行（禁止在此处生成 plan）
    result = agent.handle_message(user_message)

    # Step C: 返回 SSE stream
    return create_sse_stream(session_id)
```

**`api/app.py` 不得包含任何以下逻辑：**
- plan 生成
- itinerary 构造
- orchestrator 调用
- mock 数据返回

### Step 5 — 新建 `sse_adapter.py`

必须实现 EventBus → SSE 映射，且必须过滤 session_id：

```python
import json
from flask import Response
from event_bus import event_bus  # 引用实际 event_bus 实例

# SSE 事件映射表
EVENT_MAP = {
    "clarify":        "text",
    "plan_complete":  "itinerary_ready",
    "status_update":  "status",
    "booking_event":  "fulfill_item",
    "error":          "error",
}

def create_sse_stream(session_id: str):
    def generate():
        for event in event_bus.subscribe(session_id):
            # 关键：session 隔离过滤
            if event.get("session_id") != session_id:
                continue

            sse_type = EVENT_MAP.get(event.get("type"), "message")
            payload = json.dumps({
                "type": sse_type,
                "data": event.get("data"),
                "session_id": session_id,
            })
            yield f"event: {sse_type}\ndata: {payload}\n\n"

    return Response(generate(), mimetype="text/event-stream")
```

### Step 6 — 修改 `event_bus.py`（如果需要）

确保所有 emit 和 subscribe 支持 session_id 参数：

```python
class EventBus:
    def emit(self, event_type: str, data: dict, session_id: str):
        # session_id 是必填参数，不得有默认值 None
        event = {"type": event_type, "data": data, "session_id": session_id}
        self._dispatch(event)

    def subscribe(self, session_id: str):
        # 只 yield 属于该 session 的事件
        pass
```

---

## 五、SSE 事件映射表（完整版）

| ChatAgent / EventBus 内部事件 | SSE event 名称   | 说明                   |
|-------------------------------|------------------|------------------------|
| `clarify`                     | `text`           | 追问用户，需要更多信息 |
| `plan_complete`               | `itinerary_ready`| 行程规划完成           |
| `status_update`               | `status`         | 中间状态推送           |
| `booking_event`               | `fulfill_item`   | 单项预订完成           |
| `error`                       | `error`          | 错误信息               |
| `thinking`                    | `status`         | LLM 推理中             |

---

## 六、验证命令（每个 Step 完成后必须运行）

### 验证 Step 2：session 持久化

```bash
# 第一轮对话
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-s1", "message": "下午出去玩"}'

# 第二轮对话（同 session）
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-s1", "message": "下午2点"}'

# 期望：第二次必须触发 plan generation，出现 itinerary_ready 事件
# 失败标志：第二次重新问出发时间（说明 conversation_history 丢失）
```

### 验证 Step 4：orchestrator 已被清除

```bash
grep -rn "orchestrator" . --include="*.py"
# 期望：api/app.py 中不得出现任何 orchestrator 引用

grep -rn "mock" . --include="*.py"
# 期望：不得有直接返回 mock itinerary 的代码
```

### 验证 Step 5：session 隔离

```bash
# 终端 1：监听 s1 的 SSE
curl -N http://localhost:5000/stream?session_id=test-s1

# 终端 2：向 s2 发送消息
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-s2", "message": "帮我订机票"}'

# 期望：终端 1 不收到任何事件
# 失败标志：s1 stream 收到了 s2 的事件
```

### 验证完整 request lifecycle

```bash
# 打开 SSE 监听
curl -N "http://localhost:5000/stream?session_id=test-full" &

# 发送第一条消息
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-full", "message": "明天去上海玩"}'

# 发送第二条消息（补充信息）
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-full", "message": "早上9点出发，预算1000元"}'

# 期望 SSE 输出顺序：
# event: text        （ChatAgent 追问细节）
# event: status      （开始规划）
# event: itinerary_ready  （规划完成）
```

---

## 七、禁止行为清单（出现即删除）

| 禁止行为 | 影响文件 | 处理方式 |
|----------|----------|----------|
| `orchestrator.start_session()` | `api/app.py` | 整行删除 |
| API 层生成 plan 或 itinerary | `api/app.py` | 整段删除，移入 ChatAgent |
| 每次 request `new ChatAgent()` | 任何文件 | 替换为 `get_or_create_agent()` |
| `event_bus.emit(...)` 不带 `session_id` | `chat_agent.py` | 补充 session_id 参数 |
| SSE 层无 session_id 过滤 | `sse_adapter.py` | 添加 if 过滤 |
| 直接 `return {"itinerary": [...]}` mock | 任何文件 | 删除，必须走 ChatAgent |
| `conversation_history = []` 在 handle_message 内部 | `chat_agent.py` | 移到 `__init__` |

---

## 八、遇到架构冲突时，停止并输出以下格式

```
CONFLICT DETECTED
文件: <filename>
冲突描述: <具体描述冲突内容>
当前代码: 
    <相关代码片段>
选项A: <方案描述>
选项B: <方案描述>
需要决策: 请人工确认后继续
```

**遇到以下情况必须上报，不得自行决策：**
- `orchestrator` 与 `ChatAgent` 职责出现重叠
- `EventBus` 在当前实现中无法做到 session 隔离
- `ChatAgent` 依赖某个 long-running process 无法 stateless continuation
- 修改某文件会破坏另一个模块的现有功能

---

## 九、验收标准（全部满足才算完成）

- [ ] `ChatAgent` 是唯一的执行入口，`api/app.py` 不含业务逻辑
- [ ] `session_manager.py` 存在，且实现了 agent 复用
- [ ] `conversation_history` 跨多次 HTTP request 累积，不重置
- [ ] 同一 `session_id` 始终使用同一个 `ChatAgent` 实例
- [ ] `EventBus` 所有 emit 带 `session_id`，SSE 层有过滤
- [ ] A session 的事件不进入 B session 的 SSE stream
- [ ] 代码中无 mock itinerary 直接返回
- [ ] 代码中无 `orchestrator` 在 runtime 路径上的调用
- [ ] 多轮对话验证通过（见第六节验证命令）
- [ ] session 隔离验证通过（见第六节验证命令）
