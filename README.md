# CHWL Agent — 本地生活短时活动规划与执行 Agent

> 美团黑客松参赛项目 | 2026.06

## 项目简介

用户一句话描述下午出行需求（"带老婆孩子出去玩几个小时，别太远"），Agent 自动完成：

1. **理解意图** → 解析场景、人数、时间、偏好
2. **查询数据** → 并行获取活动、餐厅、天气、路线、排队信息
3. **规划行程** → LLM 评分排序 + 6 条合理性校验
4. **一键履约** → 并行预约、取号、模拟支付
5. **后台监控** → 7x24 盯排队、天气、预约余量
6. **异常重规划** → 排队暴增/下雨/用户疲劳 → 自动调整 + 用户确认

## 项目架构

```
┌─ 交互层 ─────────────────────────────────────┐
│  Web UI (队友已开发) ←→ FastAPI (SSE 事件流)   │
│  chat_demo.py (LLM 对话调试)                  │
│  cli_demo.py (命令行调试)                     │
├─ Orchestrator 层 ─────────────────────────────┤
│  orchestrator.py   3 阶段主循环                │
│  background_watch  7x24 监控                  │
│  confirmation_gateway  用户确认               │
│  event_bus         异步事件发布/订阅           │
├─ Core 层 ─────────────────────────────────────┤
│  llm_planner.py   DeepSeek 推理               │
│  llm_client.py    DeepSeek API 客户端          │
│  tool_registry.py  11 个工具                  │
│  state_machine.py  行程 7 态 + 节点 10 态 FSM  │
│  memory_store.py   三级记忆仓库                │
│  models.py         Pydantic 数据模型           │
│  output_validator  结构化输出校验              │
├─ 数据层 ──────────────────────────────────────┤
│  mocks/            内存 Mock (8 活动+12 餐厅)  │
│  mock_api/         队友 HTTP Mock API 服务     │
│  schemas/          6 组前端 JSON Schema        │
└───────────────────────────────────────────────┘
```

## 快速开始

### 环境准备

```bash
python -m venv venv
venv\Scripts\activate     # Windows
pip install -r requirements.txt
```

### 运行方式

| 方式 | 命令 | 说明 |
|------|------|------|
| **内存 Mock 模式** | `python scripts/cli_demo.py` | 不依赖外部服务 |
| **LLM 对话模式** | `python -X utf-8 scripts/chat_demo.py` | 需 DeepSeek API key |
| **FastAPI 服务** | `python scripts/test_server.py` | 供前端联调，端口 8000 |
| **队友 API 模式** | 先启 `mock_api/app.py`，再加 `--teammate` | 需队友服务 |

### 测试

```bash
pytest tests/ -v
# 110 个测试
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |

## API 端点

FastAPI 服务启动后 `http://localhost:8000/docs` 查看 Swagger。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/orchestrator/plan` | 开始规划 |
| GET | `/api/orchestrator/session/{id}` | 查询状态 |
| POST | `/api/orchestrator/confirm/{id}` | 确认并履约 |
| POST | `/api/orchestrator/sentiment/{id}` | 上报情绪 |
| GET | `/api/events/{id}` | SSE 事件流 |

## 项目结构

```
├── core/              核心逻辑
├── orchestrator/      编排层
├── schemas/           前端 JSON Schema
├── mocks/             内存 Mock 数据
├── mock_api/          队友 HTTP Mock API
├── scripts/           入口脚本
├── tests/             110 个 pytest
└── docs/              设计文档
```
