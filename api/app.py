"""
FastAPI Agent Backend — wraps ChatAgent from chat_demo.py for web UI
"""
from __future__ import annotations
import asyncio, json, logging, os, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agent.llm_client import LLMClient
from planner.llm_planner import LLMPlanner
from tools.registry import ToolRegistry
from mocks import MockBackend
from mocks.teammate_adapter import TeammateAPIAdapter
from agent.loop import Orchestrator
from runtime.event_bus import EventBus

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="CHWL Agent", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ─── Build ChatAgent (same as chat_demo.py) ─────────────────

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-67937130fddf4be086e73e7b2f6d293c")

llm = LLMClient(api_key=API_KEY)
planner = LLMPlanner(llm)

tools = ToolRegistry()
tools.register_mock("user_context", planner.handle_user_context)
tools.register_mock("candidates_score", planner.handle_candidates_score)
tools.register_mock("itinerary_generate", planner.handle_itinerary_generate)
tools.register_mock("itinerary_replan", planner.handle_itinerary_replan)

mem = MockBackend()
for name in ["location", "weather", "activities_search", "restaurants_search",
             "route_check", "booking_execute", "booking_status"]:
    tools.register_mock(name, getattr(mem, f"handle_{name}"))

event_bus = EventBus()
orchestrator = Orchestrator(tools, event_bus=event_bus)
sessions: dict[str, dict] = {}
agent_instances: dict[str, "ChatAgent"] = {}

# ─── SSE helpers ────────────────────────────────────────────

def sse_response(gen):
    async def stream():
        async for event in gen:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: {\"type\":\"stream_end\"}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

# ─── Session ───────────────────────────────────────────────

@app.post("/agent/session")
async def create_session():
    sid = f"sess_{uuid.uuid4().hex[:8]}"
    sessions[sid] = {"memory": {"session_facts": {}, "confirmed_preferences": {}, "derived_preferences": {}}}
    return {"session_id": sid}

@app.delete("/agent/{session_id}/reset")
async def reset_session(session_id: str):
    sessions.pop(session_id, None)
    agent_instances.pop(session_id, None)
    try: await orchestrator.cancel_session(session_id)
    except: pass
    return {"status": "reset", "session_id": session_id}

# ─── Chat SSE — reuses ChatAgent.handle_message() ──────────

@app.post("/agent/{session_id}/chat")
async def chat(session_id: str, request: Request):
    body = await request.json()
    message = body.get("message", "")
    phase_hint = body.get("phase_hint", "")

    async def event_stream():
        # Build or reuse ChatAgent instance
        if session_id not in agent_instances:
            from scripts.chat_demo import ChatAgent
            agent = ChatAgent()
            agent.session_id = ""
            agent.event_bus = event_bus
            agent.orchestrator = orchestrator
            agent_instances[session_id] = agent

        agent = agent_instances[session_id]

        # Subscribe to EventBus events and forward to SSE
        event_queue = asyncio.Queue()

        def on_event(ctx, event):
            event_queue.put_nowait(event)

        event_bus.subscribe("*", on_event)

        # Run handle_message in background
        async def run_agent():
            try:
                response = await agent.handle_message(message)
                await event_queue.put({"type": "_text_response", "text": response})
            except Exception as e:
                await event_queue.put({"type": "_text_response", "text": f"出错: {e}"})

        agent_task = asyncio.create_task(run_agent())

        # Forward events to SSE until done
        while True:
            try:
                evt = await asyncio.wait_for(event_queue.get(), timeout=30)
            except asyncio.TimeoutError:
                break

            etype = evt.get("type", "")
            data = evt.get("data", {})

            if etype == "_text_response":
                if evt.get("text"):
                    yield {"type": "text", "content": evt["text"]}
                break
            elif etype == "plan_complete":
                itin = data.get("itinerary", {})
                nodes = itin.get("nodes", [])
                fmt_nodes = [{
                    "id": n.get("node_id", ""), "poiId": n.get("poi_id", ""),
                    "name": n.get("poi_name", ""), "type": n.get("category", "activity"),
                    "startTime": n.get("scheduled_start", ""), "endTime": n.get("scheduled_end", ""),
                    "duration": n.get("duration_min", 60), "status": n.get("status", "planned"),
                    "tags": n.get("tags", []),
                } for n in nodes]
                yield {"type": "itinerary_ready", "nodes": fmt_nodes, "summary": itin.get("summary", "")}
            elif etype == "status_update":
                msg = data.get("message", "")
                if msg:
                    yield {"type": "status", "text": msg}
            elif etype == "execution_complete":
                yield {"type": "fulfillment_complete"}
            elif etype == "booking_status_changed":
                yield {"type": "fulfill_item", "id": data.get("node_id", ""),
                       "name": data.get("name", ""), "status": data.get("status", ""), "action": "reserve"}
            elif etype == "plan_failed":
                yield {"type": "error", "message": f"规划失败: {data.get('error', '')}"}
            elif etype == "node_failed":
                yield {"type": "status", "text": f"{data.get('name', '')} 遇到问题"}
            elif etype == "node_replaced":
                yield {"type": "status", "text": f"已替换为 {data.get('new_node', {}).get('name', '')}"}
            elif etype == "replan_ready":
                yield {"type": "status", "text": "调整方案已就绪"}
            elif etype == "replan_applied":
                itin2 = data.get("itinerary", {})
                nodes2 = itin2.get("nodes", [])
                fmt2 = [{"id":n.get("node_id",""),"poiId":n.get("poi_id",""),"name":n.get("poi_name",""),
                    "type":n.get("category","activity"),"startTime":n.get("scheduled_start",""),
                    "endTime":n.get("scheduled_end",""),"duration":n.get("duration_min",60),
                    "status":n.get("status","planned"),"tags":n.get("tags",[])} for n in nodes2]
                yield {"type": "itinerary_updated", "nodes": fmt2}
            elif etype == "execution_started":
                yield {"type": "status", "text": "开始预约..."}
            elif etype == "session_cancelled":
                yield {"type": "status", "text": "行程已取消"}

        # Cleanup
        event_bus.unsubscribe("*", on_event)
        if not agent_task.done():
            agent_task.cancel()

    return sse_response(event_stream())

# ─── Other endpoints ───────────────────────────────────────

@app.get("/agent/{session_id}/itinerary")
async def get_itinerary(session_id: str):
    try:
        s = await orchestrator.get_status(session_id)
        return {"nodes": [{"id":n.get("node_id",""),"poiId":n.get("poi_id",""),"name":n.get("poi_name",""),
            "type":n.get("category","activity"),"startTime":n.get("scheduled_start",""),
            "endTime":n.get("scheduled_end",""),"duration":n.get("duration_min",60),
            "status":n.get("status","planned"),"tags":n.get("tags",[])} for n in s.nodes]}
    except: return {"nodes": []}

@app.get("/agent/{session_id}/memory")
async def get_memory(session_id: str):
    s = sessions.get(session_id, {"memory": {"session_facts":{},"confirmed_preferences":{},"derived_preferences":{}}})
    return s["memory"]

@app.post("/agent/{session_id}/memory")
async def update_memory(session_id: str, request: Request):
    body = await request.json()
    sess = sessions.setdefault(session_id, {"memory": {"session_facts":{},"confirmed_preferences":{},"derived_preferences":{}}})
    scope = body.get("scope", "session_facts")
    if scope in sess["memory"]:
        sess["memory"][scope].update(body.get("updates", {}))
    return {"status": "updated", "memory": sess["memory"]}

@app.get("/agent/{session_id}/monitor/state")
async def monitor_state(session_id: str):
    try:
        s = await orchestrator.get_status(session_id)
        return {"phase": s.itinerary_state, "progress_pct": s.progress_pct}
    except: return {"phase": "none", "progress_pct": 0}

@app.post("/agent/{session_id}/fulfill")
async def fulfill(session_id: str):
    async def stream():
        try:
            await orchestrator.confirm_itinerary(session_id)
            yield {"type": "monitor_started", "message": "预约成功，后台监控已启动"}
        except Exception as e:
            yield {"type": "error", "message": str(e)}
    return sse_response(stream())

@app.post("/agent/{session_id}/confirmation/resolve")
async def resolve_confirm(session_id: str, request: Request):
    body = await request.json()
    try:
        r = await orchestrator.resolve_confirmation(body.get("request_id",""), body.get("approved",False), body.get("modifications",{}))
        return {"resolved": r}
    except: return {"resolved": False}

@app.post("/agent/{session_id}/exception/confirm")
async def exception_confirm(session_id: str, request: Request):
    async def stream():
        yield {"type": "status", "text": "方案已切换"}
    return sse_response(stream())

@app.post("/agent/{session_id}/node/action")
async def node_action(session_id: str, request: Request):
    body = await request.json()
    try:
        s = await orchestrator.get_status(session_id)
        return {"nodes": [{"id":n.get("node_id",""),"poiId":n.get("poi_id",""),"name":n.get("poi_name",""),
            "type":n.get("category","activity"),"startTime":n.get("scheduled_start",""),
            "endTime":n.get("scheduled_end",""),"duration":n.get("duration_min",60),
            "status":n.get("status","planned"),"tags":n.get("tags",[])} for n in s.nodes]}
    except: return {"nodes": []}

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
