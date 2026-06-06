"""
FastAPI Agent Backend — uses Orchestrator directly (mock, no LLM, instant)
"""
from __future__ import annotations
import asyncio, json, logging, os, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from tools.registry import ToolRegistry
from mocks import MockBackend
from agent.loop import Orchestrator, _d

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="CHWL Agent", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ─── Build Orchestrator with mock handlers (no LLM, instant) ─

backend = MockBackend()
tools = ToolRegistry()
for name in ["location", "user_context", "weather", "activities_search",
             "restaurants_search", "route_check", "candidates_score",
             "itinerary_generate", "booking_execute", "booking_status",
             "itinerary_replan"]:
    tools.register_mock(name, getattr(backend, f"handle_{name}"))

orchestrator = Orchestrator(tools)
sessions: dict[str, dict] = {}

# ─── Helpers ───────────────────────────────────────────────

def fmt_nodes(nodes) -> list:
    return [{"id":n.get("node_id",""),"poiId":n.get("poi_id",""),"name":n.get("poi_name",""),
        "type":n.get("category","activity"),"startTime":n.get("scheduled_start",""),
        "endTime":n.get("scheduled_end",""),"duration":n.get("duration_min",60),
        "status":n.get("status","planned"),"booking_status":n.get("booking_status"),
        "tags":n.get("tags",[]),"address":n.get("address","")} for n in nodes]

# ─── SSE ───────────────────────────────────────────────────

def sse_response(gen):
    async def stream():
        async for event in gen:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: {\"type\":\"stream_end\"}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"})

# ─── Session ───────────────────────────────────────────────

@app.post("/agent/session")
async def create_session():
    sid = f"sess_{uuid.uuid4().hex[:8]}"
    sessions[sid] = {"memory": {"session_facts":{}, "confirmed_preferences":{}, "derived_preferences":{}}}
    return {"session_id": sid}

@app.delete("/agent/{session_id}/reset")
async def reset_session(session_id: str):
    sessions.pop(session_id, None)
    try: await orchestrator.cancel_session(session_id)
    except: pass
    return {"status":"reset","session_id":session_id}

# ─── Chat SSE ─────────────────────────────────────────────

@app.post("/agent/{session_id}/chat")
async def chat(session_id: str, request: Request):
    body = await request.json()
    message = body.get("message","")

    async def event_stream():
        # Phase 1: plan
        sid = await orchestrator.start_session(message)

        for i in range(20):
            await asyncio.sleep(1)
            s = await orchestrator.get_status(sid)
            if s.itinerary_state == "pending_confirm":
                nodes = fmt_nodes(s.nodes)

                yield {"type": "status", "id": 1, "text": "需求已确认", "status": "done"}
                yield {"type": "status", "id": 2, "text": "搜索完成", "status": "done"}
                yield {"type": "status", "id": 3, "text": "排队已查", "status": "done"}
                yield {"type": "status", "id": 4, "text": "规划完成", "status": "done"}
                yield {"type": "itinerary_ready", "nodes": nodes, "summary": s.summary}
                return

        yield {"type": "error", "message": "规划超时"}

    return sse_response(event_stream())

# ─── Fulfill SSE ───────────────────────────────────────────

@app.post("/agent/{session_id}/fulfill")
async def fulfill(session_id: str):
    async def stream():
        try:
            await orchestrator.confirm_itinerary(session_id)
            yield {"type": "fulfill_init", "items": []}
            for i in range(10):
                await asyncio.sleep(1.5)
                s = await orchestrator.get_status(session_id)
                yield {"type": "fulfill_progress", "value": min(100, (i+1)*15)}
                if s.itinerary_state in ("completed","needs_replan"):
                    yield {"type": "monitor_started", "message": "预约成功，后台监控已启动"}
                    return
        except Exception as e:
            yield {"type": "error", "message": str(e)}
    return sse_response(stream())

# ─── REST endpoints ────────────────────────────────────────

@app.get("/agent/{session_id}/itinerary")
async def get_itinerary(session_id: str):
    try:
        s = await orchestrator.get_status(session_id)
        return {"nodes": fmt_nodes(s.nodes)}
    except: return {"nodes": []}

@app.get("/agent/{session_id}/memory")
async def get_memory(session_id: str):
    s = sessions.get(session_id, {"memory":{"session_facts":{},"confirmed_preferences":{},"derived_preferences":{}}})
    return s["memory"]

@app.post("/agent/{session_id}/memory")
async def update_memory(session_id: str, request: Request):
    body = await request.json()
    sess = sessions.setdefault(session_id, {"memory":{"session_facts":{},"confirmed_preferences":{},"derived_preferences":{}}})
    scope = body.get("scope","session_facts")
    if scope in sess["memory"]:
        sess["memory"][scope].update(body.get("updates",{}))
    return {"status":"updated","memory":sess["memory"]}

@app.get("/agent/{session_id}/monitor/state")
async def monitor_state(session_id: str):
    try:
        s = await orchestrator.get_status(session_id)
        return {"phase": s.itinerary_state, "progress_pct": s.progress_pct}
    except: return {"phase":"none","progress_pct":0}

@app.post("/agent/{session_id}/confirmation/resolve")
async def resolve_confirm(session_id: str, request: Request):
    body = await request.json()
    try:
        r = await orchestrator.resolve_confirmation(body.get("request_id",""), body.get("approved",False), body.get("modifications",{}))
        return {"resolved": r}
    except: return {"resolved":False}

@app.post("/agent/{session_id}/exception/confirm")
async def exception_confirm(session_id: str, request: Request):
    async def stream():
        yield {"type":"status","text":"方案已切换"}
    return sse_response(stream())

@app.post("/agent/{session_id}/node/action")
async def node_action(session_id: str, request: Request):
    try:
        s = await orchestrator.get_status(session_id)
        return {"nodes": fmt_nodes(s.nodes)}
    except: return {"nodes": []}

@app.post("/agent/{session_id}/node/checkin")
async def node_checkin(session_id: str, request: Request):
    return {"status":"checked_in"}

@app.post("/agent/{session_id}/report")
async def report_issue(session_id: str, request: Request):
    return {"message":"已收到反馈"}

@app.post("/agent/{session_id}/simulator/advance")
async def sim_advance(session_id: str):
    return {"status":"ok"}

@app.post("/agent/{session_id}/taxi/dispatch")
async def taxi_dispatch(session_id: str):
    return {"eta_min":8}

@app.get("/agent/{session_id}/route/estimate")
async def route_estimate(session_id: str, request: Request):
    params = dict(request.query_params)
    result = await backend.handle("route_check",{"origin":params.get("from",""),"destinations":[params.get("to","")]})
    return result.get("data",{}) if result else {}

@app.get("/agent/{session_id}/queue-advice")
async def queue_advice(session_id: str):
    return {"advices":[]}

@app.get("/api/location/current")
async def user_location():
    return {"lat":39.998,"lng":116.481,"address":"北京市朝阳区望京"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)
