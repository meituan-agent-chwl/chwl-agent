"""
FastAPI Agent Backend — 严格按照 CLAUDE.md 约束实现

核心规则:
- ChatAgent 是唯一执行入口
- api/app.py 只做路由，不包含业务逻辑
- SSE 事件通过 sse_adapter.py 转发
"""
from __future__ import annotations

import asyncio, json, logging, os, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from session_manager import get_or_create_agent, destroy_agent
from sse_adapter import _fmt_node, CATEGORY_ICONS
from mocks import MockBackend

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="CHWL Agent", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

backend = MockBackend()

# ─── SSE 通用响应 ──────────────────────────────────────────

def sse_response(event_queue: asyncio.Queue, cleanup=None):
    """从事件队列创建 SSE 流"""
    async def generate():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=60)
                except asyncio.TimeoutError:
                    break
                # 转换内部事件类型为前端期望的类型
                etype = event.get("type", "")
                if etype == "_text_response":
                    event = {"type": "text", "content": event.get("text", "")}
                # 流式事件直接转发
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") == "stream_end":
                    break
        finally:
            if cleanup:
                cleanup()
    return StreamingResponse(generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

# ─── Session ───────────────────────────────────────────────

@app.post("/agent/session")
async def create_session():
    sid = f"sess_{uuid.uuid4().hex[:8]}"
    return {"session_id": sid}

@app.delete("/agent/{session_id}/reset")
async def reset_session(session_id: str):
    destroy_agent(session_id)
    return {"status": "reset", "session_id": session_id}

# ─── Chat SSE — 唯一入口，通过 ChatAgent.handle_message() ──

@app.post("/agent/{session_id}/chat")
async def chat(session_id: str, request: Request):
    body = await request.json()
    message = body.get("message", "")

    # Step A: 获取或创建 agent（唯一允许的逻辑）
    agent = get_or_create_agent(session_id)

    # Step B: 创建 session-scoped SSE 事件通道
    event_queue, cleanup = create_sse_session(agent.event_bus, session_id)

    # Step C: 异步执行 ChatAgent（禁止在此处生成 plan）
    async def run_agent():
        try:
            response = await agent.handle_message(message)
            await event_queue.put({"type": "_text_response", "text": response})
        except Exception as e:
            await event_queue.put({"type": "_text_response", "text": f"出错: {e}"})
        finally:
            await event_queue.put({"type": "stream_end"})

    asyncio.create_task(run_agent())

    # Step D: 返回 SSE 流（事件由 sse_adapter 转发）
    return sse_response(event_queue, cleanup)

# ─── Fulfill SSE ───────────────────────────────────────────

@app.post("/agent/{session_id}/fulfill")
async def fulfill(session_id: str):
    async def stream():
        try:
            agent = get_or_create_agent(session_id)
            result = await agent.orchestrator.confirm_itinerary(session_id)
            yield {"type": "fulfill_init", "items": []}

            for i in range(12):
                await asyncio.sleep(1.5)
                s = await agent.orchestrator.get_status(session_id)
                yield {"type": "fulfill_progress", "value": min(100, (i+1)*12)}
                if s.itinerary_state in ("completed", "needs_replan"):
                    yield {"type": "monitor_started", "message": "预约成功，后台监控已启动"}
                    return
        except Exception as e:
            yield {"type": "error", "message": str(e)}

    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

# ─── REST ──────────────────────────────────────────────────

@app.get("/agent/{session_id}/itinerary")
async def get_itinerary(session_id: str):
    try:
        agent = get_or_create_agent(session_id)
        s = await agent.orchestrator.get_status(session_id)
        return {"nodes": [{"id":n.get("node_id",""),"poiId":n.get("poi_id",""),"name":n.get("poi_name",""),
            "type":n.get("category","activity"),"startTime":n.get("scheduled_start","") or n.get("start_time",""),
            "endTime":n.get("scheduled_end","") or n.get("end_time",""),"duration":n.get("duration_min",60),
            "status":n.get("status","planned"),"tags":n.get("tags",[])} for n in s.nodes]}
    except:
        return {"nodes": []}

@app.get("/agent/{session_id}/memory")
async def get_memory(session_id: str):
    return {"memory": {"session_facts":{}, "confirmed_preferences":{}, "derived_preferences":{}}}

@app.post("/agent/{session_id}/memory")
async def update_memory(session_id: str, request: Request):
    body = await request.json()
    return {"status": "updated"}

@app.get("/agent/{session_id}/monitor/state")
async def monitor_state(session_id: str):
    try:
        agent = get_or_create_agent(session_id)
        s = await agent.orchestrator.get_status(session_id)
        return {"phase": s.itinerary_state, "progress_pct": s.progress_pct}
    except:
        return {"phase": "none", "progress_pct": 0}

@app.post("/agent/{session_id}/confirmation/resolve")
async def resolve_confirm(session_id: str, request: Request):
    body = await request.json()
    try:
        agent = get_or_create_agent(session_id)
        r = await agent.orchestrator.resolve_confirmation(
            body.get("request_id",""), body.get("approved",False), body.get("modifications",{}))
        return {"resolved": r}
    except:
        return {"resolved": False}

@app.post("/agent/{session_id}/exception/confirm")
async def exception_confirm(session_id: str, request: Request):
    async def stream():
        yield {"type": "status", "text": "方案已切换"}
    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

@app.post("/agent/{session_id}/node/action")
async def node_action(session_id: str, request: Request):
    try:
        agent = get_or_create_agent(session_id)
        s = await agent.orchestrator.get_status(session_id)
        return {"nodes": [{"id":n.get("node_id",""),"poiId":n.get("poi_id",""),"name":n.get("poi_name",""),
            "type":n.get("category","activity"),"startTime":n.get("scheduled_start","") or n.get("start_time",""),
            "endTime":n.get("scheduled_end","") or n.get("end_time",""),"duration":n.get("duration_min",60),
            "status":n.get("status","planned"),"tags":n.get("tags",[])} for n in s.nodes]}
    except:
        return {"nodes": []}

@app.post("/agent/{session_id}/node/replace")
async def node_replace(session_id: str, request: Request):
    """替换行程中的某个节点为新的 POI"""
    try:
        body = await request.json()
        node_id = body.get("node_id", "")
        new_poi_id = body.get("new_poi_id", "")
        if not node_id or not new_poi_id:
            return {"success": False, "error": "缺少 node_id 或 new_poi_id"}
        agent = get_or_create_agent(session_id)
        # 从 raw_candidates 中找新 POI 的数据
        ctx = agent.orchestrator.sessions.get(session_id)
        new_resource = None
        if ctx and hasattr(ctx, "raw_candidates"):
            for c in ctx.raw_candidates:
                if c.get("poi_id") == new_poi_id:
                    new_resource = {
                        "poi_id": new_poi_id,
                        "poi_name": c.get("name", ""),
                        "address": c.get("address", ""),
                        "category": c.get("category", ""),
                        "duration_min": c.get("estimated_duration_min", 60),
                        "tags": c.get("tags", []),
                        "rating": c.get("rating", 0),
                        "distance_km": c.get("distance_km", 0),
                        "ticket_price": c.get("ticket_price", c.get("avg_price", 0)),
                    }
                    break
        if new_resource:
            from schemas.models import ItineraryModification
            mod = ItineraryModification(type="replace", node_id=node_id, new_resource=new_resource)
            await agent.orchestrator.modify_itinerary(session_id, mod)
        # 返回更新后的节点（使用 _fmt_node 格式，保持与 SSE adapter 一致）
        if ctx and ctx.itinerary:
            fmt_nodes = []
            for node_m in ctx.itinerary.nodes:
                nd = node_m.model_dump() if hasattr(node_m, "model_dump") else node_m.dict()
                fmt_nodes.append(_fmt_node(nd))
            return {"success": True, "nodes": fmt_nodes}
        return {"success": True, "nodes": []}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/agent/{session_id}/node/checkin")
async def node_checkin(session_id: str, request: Request):
    return {"status": "checked_in"}

@app.post("/agent/{session_id}/report")
async def report_issue(session_id: str, request: Request):
    return {"message": "已收到反馈"}

@app.post("/agent/{session_id}/simulator/advance")
async def sim_advance(session_id: str):
    return {"status": "ok"}

@app.post("/agent/{session_id}/taxi/dispatch")
async def taxi_dispatch(session_id: str):
    return {"eta_min": 8}

@app.get("/agent/{session_id}/route/estimate")
async def route_estimate(session_id: str, request: Request):
    params = dict(request.query_params)
    result = await backend.handle("route_check", {"origin": params.get("from",""), "destinations": [params.get("to","")]})
    return result.get("data", {}) if result else {}

@app.get("/agent/{session_id}/queue-advice")
async def queue_advice(session_id: str):
    return {"advices": []}

@app.get("/api/location/current")
async def user_location():
    return {"lat": 39.998, "lng": 116.481, "address": "北京市朝阳区望京"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)
