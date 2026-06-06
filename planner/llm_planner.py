"""LLM Planner — intent, scoring, generation, replanning"""
from __future__ import annotations
import json, logging, uuid
from typing import Optional
from agent.llm_client import LLMClient
from planner.prompts import *

logger = logging.getLogger(__name__)

class LLMPlanner:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def handle_user_context(self, payload: dict) -> dict:
        user_input = payload.get("input", "")
        try:
            result = await self.llm.chat_json(system=PREFERENCE_EXTRACTION_SYSTEM,
                messages=[{"role": "user", "content": user_input}])
            scene = result.get("scene", "family")
            if scene not in ("family", "friends", "couple", "solo"):
                scene = "family"
            companions = result.get("companions", [])
            has_child = any(c.get("type") == "child" for c in companions)
            mode = "full_managed" if has_child else "light_managed"
            return {"success": True, "data": {
                "scene": scene, "time_range": "afternoon", "distance_constraint": "nearby",
                "companions_text": user_input, "companions": companions,
                "special_requirements": result.get("special_requirements", []),
                "missing_info": result.get("missing_info", []),
                "intent_conflict": False, "mode": mode, "start_time": result.get("start_time", "14:00"),
            }}
        except Exception as e:
            logger.error("[LLMPlanner] user_context 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    async def handle_candidates_score(self, payload: dict) -> dict:
        candidates, user_context = payload.get("candidates", []), payload.get("user_context", {})
        valid_ids = {c.get("poi_id") for c in candidates if c.get("poi_id")}
        try:
            result = await self.llm.chat_json(system=SCORING_SYSTEM, messages=[{"role":"user","content":json.dumps({
                "candidates": candidates, "user_scenario": user_context.get("scene", "family"),
                "special_requirements": user_context.get("special_requirements", []),
                "weather": user_context.get("weather", "晴"), "temperature": 25,
            }, ensure_ascii=False)}])
            scored = result.get("scored", [])
            validated = [s for s in scored if s.get("poi_id") in valid_ids]
            return {"success": True, "data": {"scored": validated}}
        except Exception as e:
            logger.error("[LLMPlanner] scoring 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    async def handle_itinerary_generate(self, payload: dict) -> dict:
        selected, departure = payload.get("selected_nodes", {}), payload.get("departure_time", "14:00")
        user_feedback = payload.get("user_feedback", "")
        valid_ids = set()
        cat_map = {}
        for key in ("main_activity", "restaurant", "optional_activity"):
            n = selected.get(key, {})
            if isinstance(n, dict) and n.get("poi_id"):
                valid_ids.add(n["poi_id"]); cat_map[n["poi_id"]] = key
        try:
            result = await self.llm.chat_json(system=ITINERARY_GENERATION_SYSTEM, messages=[{"role":"user","content":json.dumps({
                "departure_time":departure,"valid_poi_ids":list(valid_ids),
                "main_activity":selected.get("main_activity",{}),"restaurant":selected.get("restaurant",{}),
                "optional_activity":selected.get("optional_activity",{}),"user_feedback":user_feedback,
            },ensure_ascii=False)}])
            nodes = result.get("nodes", [])
            validated = [n for n in nodes if n.get("poi_id") in valid_ids]
            has_rest = any(n.get("category") == "restaurant" for n in validated)
            if not has_rest:
                rest_data = selected.get("restaurant", {})
                if rest_data and rest_data.get("poi_id"):
                    validated.append({"node_id":f"node_{uuid.uuid4()}","poi_id":rest_data["poi_id"],
                        "poi_name":rest_data.get("name","餐厅"),"category":"restaurant",
                        "start_time":"","end_time":"","duration_min":60,"tags":[]})
            return {"success":True,"data":{"itinerary_id":f"iti_{uuid.uuid4().hex[:8]}","nodes":validated,
                "total_duration_min":result.get("total_duration_min",180),"summary":result.get("summary","")}}
        except Exception as e:
            logger.error("[LLMPlanner] generate 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    async def handle_itinerary_replan(self, payload: dict) -> dict:
        try:
            trigger = payload.get("trigger", {})
            feedback = payload.get("user_feedback", trigger.get("description", ""))
            result = await self.llm.chat_json(system=REPLAN_SYSTEM, messages=[{"role":"user","content":json.dumps({
                "trigger_reason":trigger.get("type","unknown"),"user_feedback":feedback,
                "locked_nodes":payload.get("policy",{}).get("locked_nodes",[]),
                "pending_nodes":payload.get("policy",{}).get("pending_nodes",[]),
            },ensure_ascii=False)}])
            return {"success":True,"data":{"replan_id":f"rep_{uuid.uuid4().hex[:8]}",
                "need_user_confirm":result.get("need_user_confirm",True),"nodes":result.get("nodes",[]),
                "updated_route_required":True}}
        except Exception as e:
            logger.error("[LLMPlanner] replan 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    async def generate_response(self, state: str, user_message: str) -> str:
        try:
            return await self.llm.chat(system=RESPONSE_SYSTEM,
                messages=[{"role":"user","content":json.dumps({"state":state,"user_said":user_message},ensure_ascii=False)}])
        except Exception:
            return "好的，我看看（系统处理中）"
