"""
FastAPI Agent Backend — /agent/* for frontend, /api/* for backward compatibility
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from tools.registry import ToolRegistry
from mocks import MockBackend
from agent.loop import Orchestrator, _d
from runtime.event_bus import EventBus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CHWL Agent Backend", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# ─── Dependencies ──────────────────────────────────────────

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

def get_session(session_id: str):
    if session_id not in sessions:
        sessions[session_id] = {"memory": {"session_facts": {}, "confirmed_preferences": {}, "derived_preferences": {}}}
    return sessions[session_id]

def sse_response(gen):
    async def stream():
        async for event in gen:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: {\"type\":\"stream_end\"}\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

# ─── Health ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "chwl-agent", "version": "2.0.0"}

# ─── Session ───────────────────────────────────────────────

@app.post("/agent/session")
async def create_session():
    sid = f"sess_{uuid.uuid4().hex[:8]}"
    sessions[sid] = {"memory": {"session_facts": {}, "confirmed_preferences": {}, "derived_preferences": {}}}
    return {"session_id": sid}

@app.delete("/agent/{session_id}/reset")
async def reset_session(session_id: str):
    sessions.pop(session_id, None)
    try:
        await orchestrator.cancel_session(session_id)
    except Exception:
        pass
    return {"status": "reset", "session_id": session_id}

# ─── Chat SSE (primary entry) ──────────────────────────────

@app.post("/agent/{session_id}/chat")
async def chat(session_id: str, request: Request):
    body = await request.json()
    message = body.get("message", "")
    phase_hint = body.get("phase_hint", "")

    async def event_stream():
        # Map frontend phase hints to Orchestrator actions
        if phase_hint == "start_plan" or not session_id:
            sid = await orchestrator.start_session(message)
            yield {"type": "status", "message": "正在为您搜索活动..."}
            # Wait for Phase 1
            for _ in range(30):
                await asyncio.sleep(1)
                s = await orchestrator.get_status(sid)
                if s.itinerary_state == "pending_confirm":
                    yield {"type": "plan_complete", "itinerary": _d(s)}
                    yield {"type": "phase_change", "phase": "confirming"}
                    break
        else:
            # Handle other phases
            s = await orchestrator.get_status(session_id)
            if s.itinerary_state == "pending_confirm":
                yield {"type": "plan_complete", "itinerary": _d(s)}

    return sse_response(event_stream())

# ─── Plan (SSE) ────────────────────────────────────────────

@app.post("/agent/{session_id}/plan")
async def plan(session_id: str, request: Request):
    body = await request.json()
    user_input = body.get("user_input", body.get("message", ""))

    async def event_stream():
        sid = await orchestrator.start_session(user_input)
        yield {"type": "status", "message": "正在搜索活动..."}
        for _ in range(30):
            await asyncio.sleep(1)
            s = await orchestrator.get_status(sid)
            if s.itinerary_state == "pending_confirm":
                yield {"type": "plan_complete", "itinerary": _d(s), "session_id": sid}
                break

    return sse_response(event_stream())

# ─── Fulfill (SSE) ─────────────────────────────────────────

@app.post("/agent/{session_id}/fulfill")
async def fulfill(session_id: str):
    async def event_stream():
        try:
            await orchestrator.confirm_itinerary(session_id)
            yield {"type": "fulfillment_started"}
            for _ in range(20):
                await asyncio.sleep(1.5)
                s = await orchestrator.get_status(session_id)
                if s.itinerary_state in ("completed", "needs_replan"):
                    yield {"type": "fulfillment_complete", "itinerary": _d(s)}
                    break
        except Exception as e:
            yield {"type": "error", "message": str(e)}

    return sse_response(event_stream())

# ─── Confirmation ──────────────────────────────────────────

@app.post("/agent/{session_id}/confirmation/resolve")
async def resolve_confirmation(session_id: str, request: Request):
    body = await request.json()
    try:
        result = await orchestrator.resolve_confirmation(
            body.get("request_id", ""), body.get("approved", False), body.get("modifications", {})
        )
        return {"resolved": result}
    except Exception as e:
        return {"resolved": False, "error": str(e)}

@app.post("/agent/{session_id}/exception/confirm")
async def exception_confirm(session_id: str, request: Request):
    body = await request.json()
    async def event_stream():
        try:
            await orchestrator.resolve_confirmation(
                body.get("request_id", ""), body.get("approved", False), body.get("modifications", {})
            )
            yield {"type": "exception_resolved"}
        except Exception as e:
            yield {"type": "error", "message": str(e)}
    return sse_response(event_stream())

# ─── Itinerary read ────────────────────────────────────────

@app.get("/agent/{session_id}/itinerary")
async def get_itinerary(session_id: str):
    try:
        s = await orchestrator.get_status(session_id)
        return {"nodes": s.nodes}
    except Exception:
        return {"nodes": []}

# ─── Memory ────────────────────────────────────────────────

@app.get("/agent/{session_id}/memory")
async def get_memory(session_id: str):
    sess = get_session(session_id)
    return sess["memory"]

@app.post("/agent/{session_id}/memory")
async def update_memory(session_id: str, request: Request):
    body = await request.json()
    sess = get_session(session_id)
    scope = body.get("scope", "session_facts")
    updates = body.get("updates", {})
    if scope in sess["memory"]:
        sess["memory"][scope].update(updates)
    return {"status": "updated", "memory": sess["memory"]}

# ─── Node action ───────────────────────────────────────────

@app.post("/agent/{session_id}/node/action")
async def node_action(session_id: str, request: Request):
    body = await request.json()
    # Simplified: just return current nodes (full editing requires confirmation gateway)
    try:
        s = await orchestrator.get_status(session_id)
        return {"nodes": s.nodes}
    except Exception:
        return {"nodes": []}

@app.post("/agent/{session_id}/node/checkin")
async def node_checkin(session_id: str, request: Request):
    return {"status": "checked_in"}

# ─── Monitor ───────────────────────────────────────────────

@app.get("/agent/{session_id}/monitor/state")
async def monitor_state(session_id: str):
    try:
        s = await orchestrator.get_status(session_id)
        return {"phase": s.itinerary_state, "itinerary": s.nodes, "progress_pct": s.progress_pct}
    except Exception:
        return {"phase": "none", "itinerary": [], "progress_pct": 0}

# ─── MISC ──────────────────────────────────────────────────

@app.post("/agent/{session_id}/report")
async def report_issue(session_id: str, request: Request):
    return {"status": "reported"}

@app.post("/agent/{session_id}/simulator/advance")
async def simulator_advance(session_id: str):
    return {"status": "advanced"}

@app.post("/agent/{session_id}/taxi/dispatch")
async def taxi_dispatch(session_id: str):
    return {"status": "dispatched", "eta_min": 8}

@app.get("/agent/{session_id}/route/estimate")
async def route_estimate(session_id: str, request: Request):
    params = dict(request.query_params)
    result = await backend.handle("route_check", {
        "origin": params.get("from", ""), "destinations": [params.get("to", "")]
    })
    return result.get("data", {})

@app.get("/agent/{session_id}/queue-advice")
async def queue_advice(session_id: str):
    return {"advices": []}

@app.get("/api/location/current")
async def user_location():
    return {"lat": 39.998, "lng": 116.481, "address": "北京市朝阳区望京"}

# ─── Legacy /api/* endpoints (keep for backward compat) ───

@app.get("/api/events/{session_id}")
async def sse_events(session_id: str):
    return {"status": "ok"}

@app.post("/api/orchestrator/plan")
async def legacy_plan(request: Request):
    body = await request.json()
    sid = await orchestrator.start_session(body.get("user_input", ""))
    return {"session_id": sid, "status": "planning"}

@app.get("/api/orchestrator/session/{session_id}")
async def legacy_session(session_id: str):
    return _d(await orchestrator.get_status(session_id))

@app.post("/api/orchestrator/confirm/{session_id}")
async def legacy_confirm(session_id: str):
    return await orchestrator.confirm_itinerary(session_id)

@app.post("/api/orchestrator/cancel/{session_id}")
async def legacy_cancel(session_id: str):
    return await orchestrator.cancel_session(session_id)

# ─── Entry ─────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.app:app", host="0.0.0.0", port=8000, reload=True)
