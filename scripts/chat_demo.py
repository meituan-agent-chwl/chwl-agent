"""
Chat Demo — LLM 驱动的 AI Agent 对话交互

真正的 Agent 循环：
  用户输入 → LLM 理解意图 → 执行操作（规划/履约/重规划/展示）→ 回复用户

用法:
    python -X utf-8 scripts/chat_demo.py

架构:
  handle_message → LLM 意图路由 → action dispatch → Orchestrator API → 回复用户
                   (30行 if/elif 替换为 Agent 循环)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm_client import LLMClient
from planner.llm_planner import LLMPlanner
from tools.registry import ToolRegistry
from schemas.models import UserSentiment
from mocks import MockBackend
from mocks.teammate_adapter import TeammateAPIAdapter
from agent.loop import Orchestrator
from runtime.event_bus import EventBus

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-67937130fddf4be086e73e7b2f6d293c")


# ── Agent System Prompt ────────────────────────────────────

AGENT_PROMPT = """你是执行型助手「小美」，负责解析用户意图并路由到正确的后端操作。

【操作列表】
- plan: 用户给出出发时间 + 人数/人员构成时，立即触发
- clarify: 缺少出发时间或出行人数时，追问（每次只问一个信息）
- show_plan: 用户想看当前方案时
- replan: 用户对方案不满意，或提出新偏好/约束时
- confirm: 用户明确表示接受方案时
- cancel: 用户要取消或结束时
- chat: 其他情况

【触发规则——按优先级从高到低】

优先级 1（强制 clarify）:
- 用户表达出行意图，但没有出发时间 → clarify，问出发时间
- 用户表达出行意图，但没有人数/人员 → clarify，问几个人

优先级 2（强制 plan）:
- 用户同时给出了时间 + 人员信息 → plan，不要聊天
- 用户说"你安排""随便""都行" → plan（使用已知信息）

优先级 3（强制 replan）:
- 用户说"太早/太晚/太赶/换一个/重新规划/不喜欢" → replan
- 用户提出新偏好（"想吃火锅""不想走路"）→ replan

优先级 4（强制 show_plan）:
- 用户说"行程呢/结果呢/看看/方案在哪/展示一下" → show_plan

优先级 5（强制 confirm）:
- 用户说"好/行/确认/就这样/安排/可以" → confirm

【关键约束】
- plan 不是让你描述方案，是让后端去搜索真实数据
- clarify 每次只问一个问题，不要一次问多个
- replan 前不需要再次 clarify，直接用新偏好触发 replan
- response 最多 1-2 句，不列出行程细节

【当前系统状态】
行程状态: {state}
当前方案摘要: {plan_summary}

【输出格式（严格 JSON）】
{{"action": "plan|clarify|show_plan|replan|confirm|cancel|chat", "response": "对用户说的话，1-2句", "reason": "识别到的用户意图"}}
"""


# ── 安全打印（兼容 GBK） ──────────────────────────────────

def sp(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        safe = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        try:
            print(safe)
        except Exception:
            pass


# ── Agent ──────────────────────────────────────────────────

class ChatAgent:
    """LLM 驱动的 Agent 对话循环"""

    def __init__(self, session_id: str = "", use_teammate: bool = False, mock_llm: bool = True):
        self.llm = LLMClient(api_key=API_KEY)
        self.planner = LLMPlanner(self.llm)
        self.session_id = session_id
        self.conversation_history: list[dict] = []
        self._plan_id: str | None = None
        self._planning: bool = False
        self.mock_llm = mock_llm

        self.event_bus = EventBus()
        self.orchestrator = self._build(use_teammate)
        self._subscribe_events()

    def _build(self, use_teammate: bool) -> Orchestrator:
        tools = ToolRegistry()
        # 根据模式注册 LLM 工具：mock 模式用纯逻辑（不调 DeepSeek）
        if self.mock_llm:
            tools.register_mock("user_context", self._mock_user_context)
            tools.register_mock("candidates_score", self._mock_candidates_score)
            tools.register_mock("itinerary_generate", self._mock_itinerary_generate)
            tools.register_mock("itinerary_replan", self._mock_itinerary_replan)
        else:
            tools.register_mock("user_context", self.planner.handle_user_context)
            tools.register_mock("candidates_score", self.planner.handle_candidates_score)
            tools.register_mock("itinerary_generate", self.planner.handle_itinerary_generate)
            tools.register_mock("itinerary_replan", self.planner.handle_itinerary_replan)

        if use_teammate:
            adapter = TeammateAPIAdapter("http://127.0.0.1:8000")
            for name in ["activities_search", "restaurants_search", "weather",
                         "route_check", "booking_status", "booking_execute"]:
                tools.register_mock(name, getattr(adapter, name))
        else:
            mem = MockBackend()
            for name in ["location", "weather", "activities_search",
                         "restaurants_search", "route_check",
                         "booking_execute", "booking_status"]:
                tools.register_mock(name, getattr(mem, f"handle_{name}"))
        return Orchestrator(tools, event_bus=self.event_bus)

    # ── Mock LLM handlers（不调 DeepSeek，纯逻辑，<50ms） ──

    async def _mock_user_context(self, payload: dict) -> dict:
        """纯文本规则提取用户意图"""
        text = payload.get("input", "")
        has_friend = "朋友" in text
        has_child = any(k in text for k in ("孩子", "小孩", "儿童", "宝宝", "娃"))
        has_weight_loss = any(k in text for k in ("减肥", "瘦", "减脂"))
        has_light = any(k in text for k in ("清淡", "健康", "轻食"))
        special = []
        if has_weight_loss or has_light:
            special.append("减肥" if has_weight_loss else "清淡")
        scene = "friends" if has_friend and not has_child else ("family" if has_child else "solo")
        mode = "full_managed" if has_child else "light_managed"
        # 从输入里提取出发时间
        start_time = "14:00"
        import re as _re
        tm = _re.search(r"(\d+)[:：点](\d*)|(\d+)[:：点]?", text)
        if tm:
            h = int(tm.group(1) or tm.group(3) or "14")
            m = int(tm.group(2)) if tm.group(2) else 0
            # 修复：口语"2点"=下午2点不是凌晨2点，小时<7且无"上午/早"语境时+12
            has_am_context = any(k in text for k in ("上午", "早上", "早起", "一早"))
            if h < 7 and not has_am_context:
                h += 12
            start_time = f"{h:02d}:{m:02d}"
        return {"success": True, "data": {
            "scene": scene, "time_range": "afternoon", "distance_constraint": "nearby",
            "companions_text": text,
            "companions": [{"type": "child", "age": 6}] if has_child else (
                [{"type": "friend"}] if has_friend else []),
            "special_requirements": special,
            "missing_info": [] if start_time or any(k in text for k in ("点", ":")) else ["start_time"],
            "intent_conflict": False, "mode": mode, "start_time": start_time,
        }}

    async def _mock_candidates_score(self, payload: dict) -> dict:
        """规则评分（LLM prompt 里的逻辑直接代码化）"""
        candidates = payload.get("candidates", [])
        scene = (payload.get("user_context") or {}).get("scene", "family")
        scored = []
        for c in candidates:
            score = 50
            tags_str = " ".join(c.get("tags", []))
            if scene == "family":
                if c.get("child_friendly") or "亲子" in tags_str or "儿童" in tags_str:
                    score += 25
                if "室内" in tags_str:
                    score += 15
            else:
                if any(t in tags_str for t in ("社交", "氛围", "拍照")):
                    score += 15
            q = c.get("queue_time_min", 0)
            if q > 60:
                score -= 100  # disqualified
            elif q > 30:
                score -= 25
            d = c.get("distance_km", 0)
            if d > 10:
                score -= 25
            elif d > 5:
                score -= 10
            # 健康/低卡加分
            if any(t in tags_str for t in ("低卡", "清淡", "健康", "轻食")):
                score += 10
            score = max(0, min(100, score))
            scored.append({
                "poi_id": c.get("poi_id", ""),
                "score": score,
                "tags_matched": [],
                "planner_reason": self._mock_reason(c, scene),
                "recommended": score >= 70,
                "disqualified_reason": "排队过长" if q > 60 else None,
            })
        scored.sort(key=lambda s: s["score"], reverse=True)
        return {"success": True, "data": {"scored": scored}}

    def _mock_reason(self, c: dict, scene: str) -> str:
        parts = []
        if scene == "family" and c.get("child_friendly"):
            parts.append("适合亲子")
        q = c.get("queue_time_min", 0)
        parts.append(f"排队{q}分钟" if q > 0 else "无需排队")
        d = c.get("distance_km", 0)
        parts.append(f"{d:.1f}km" if d > 0 else "")
        return "，".join(p for p in parts if p)

    async def _mock_itinerary_generate(self, payload: dict) -> dict:
        """规则行程编排 — 严格对照 PROMPT_REDESIGN.md Prompt 4 六步规则"""
        selected = payload.get("selected_nodes", {})
        departure = payload.get("departure_time", "14:00")
        scene = payload.get("scene", "family")
        user_feedback = payload.get("user_feedback", "")
        has_child = any(k in user_feedback for k in ("孩子", "小孩", "儿童", "宝宝"))
        nodes = []
        dh, dm = map(int, departure.split(":") if ":" in departure else ("14", "00"))
        dep = dh * 60 + dm
        buf = 15  # Step 3: 交通缓冲 ≥ 15min（所有场景强制执行）

        # Step 1: 出发时间 → 确定用餐模式
        if dep >= 13 * 60:                     # >= 13:00 → 晚餐模式
            meal_mode = "dinner"
            rest_window = (17 * 60 + 30, 19 * 60 + 30)  # 17:30–19:30
        elif dep <= 11 * 60:                   # <= 11:00 → 午餐先
            meal_mode = "lunch"
            rest_window = (11 * 60 + 30, 13 * 60)       # 11:30–13:00
        else:                                  # 11:00–13:00 → 先活动后午餐
            meal_mode = "lunch_mid"
            rest_window = (12 * 60 + 30, 13 * 60 + 30)  # 12:30–13:30

        def mk_node(src: dict, cat: str, s: int, e: int) -> dict:
            d = e - s
            return {
                "node_id": f"n_{len(nodes)+1}",
                "poi_id": src.get("poi_id", ""),
                "poi_name": src.get("name", ""),
                "category": cat,
                "start_time": f"{s//60:02d}:{s%60:02d}",
                "end_time": f"{e//60:02d}:{e%60:02d}",
                "duration_min": d,
                "tags": src.get("tags", []),
                "planner_reason": src.get("planner_reason", ""),
                "address": src.get("address", ""),
                "distance_km": src.get("distance_km", 0),
                "rating": src.get("rating", 0),
                "ticket_price": src.get("ticket_price", src.get("avg_price", 0)),
                "avg_price": src.get("avg_price", 0),
            }

        def add_activity(src: dict, cat: str, fixed_start: int = None):
            """安排一个活动节点，可指定固定开始时间"""
            nonlocal nodes
            raw_dur = src.get("estimated_duration_min",
                              90 if cat == "main_activity" else 60)
            # Step 2: 有儿童时单次活动 ≤ 90min
            if has_child and cat != "restaurant":
                raw_dur = min(raw_dur, 90)
            prev_end = nodes[-1]["_end"] if nodes else dep
            if fixed_start is not None:
                start = max(fixed_start, prev_end + buf)
            else:
                start = prev_end + buf
            end = start + raw_dur
            node = mk_node(src, cat, start, end)
            node["_end"] = end  # 内部用，不出现在输出里
            nodes.append(node)

        main_act = selected.get("main_activity", {})
        restaurant = selected.get("restaurant", {})
        opt_act = selected.get("optional_activity", {})

        # ── Step 1 分支执行 ──
        if meal_mode == "dinner":
            # departure >= 13:00 → 跳过午餐，晚餐 17:30–19:30
            if main_act.get("poi_id"):
                add_activity(main_act, "main_activity")
            if opt_act.get("poi_id"):
                add_activity(opt_act, "optional_activity")
            if restaurant.get("poi_id"):
                rest_dur = min(restaurant.get("estimated_duration_min", 60), 90)
                r_start = max(rest_window[0], (nodes[-1]["_end"] if nodes else dep) + buf)
                if r_start + rest_dur > rest_window[1]:
                    r_start = rest_window[1] - rest_dur
                add_activity(restaurant, "restaurant", fixed_start=r_start)

        elif meal_mode == "lunch":
            # departure <= 11:00 → 先午餐 11:30–13:00
            if restaurant.get("poi_id"):
                add_activity(restaurant, "restaurant", fixed_start=rest_window[0])
            if main_act.get("poi_id"):
                add_activity(main_act, "main_activity")
            if opt_act.get("poi_id"):
                add_activity(opt_act, "optional_activity")

        else:  # lunch_mid — 先活动，12:30-13:30 吃饭
            if main_act.get("poi_id"):
                add_activity(main_act, "main_activity")
            if restaurant.get("poi_id"):
                add_activity(restaurant, "restaurant", fixed_start=rest_window[0])
            if opt_act.get("poi_id"):
                add_activity(opt_act, "optional_activity")

        # Step 5: 自检 — 清理内部字段 + 确保餐厅在合法窗口
        for n in nodes:
            n.pop("_end", None)
            if n["category"] == "restaurant":
                hs, ms = map(int, n["start_time"].split(":"))
                start_min = hs * 60 + ms
                if start_min < rest_window[0] or start_min > rest_window[1]:
                    n["start_time"] = f"{rest_window[0]//60:02d}:{rest_window[0]%60:02d}"
                    n["end_time"] = f"{(rest_window[0]+n['duration_min'])//60:02d}:{(rest_window[0]+n['duration_min'])%60:02d}"

        return {"success": True, "data": {
            "itinerary_id": "iti_mock",
            "nodes": nodes,
            "total_duration_min": max(0, sum(n["duration_min"] for n in nodes)),
            "summary": " → ".join(n["poi_name"] for n in nodes[:3]) + f"，共{sum(n['duration_min'] for n in nodes)}分钟",
        }}

    async def _mock_itinerary_replan(self, payload: dict) -> dict:
        """重规划 — 直接返回原方案（mock 模式不需要真重规划）"""
        return {"success": True, "data": {
            "replan_id": "rep_mock",
            "need_user_confirm": False,
            "nodes": [],
            "updated_route_required": False,
        }}

    def _subscribe_events(self):
        self.event_bus.subscribe("status_update", lambda ctx, e: sp(f"  . {e['data'].get('message','')}"))
        self.event_bus.subscribe("tool_warning", lambda ctx, e: sp(f"  [!] {e['data']['tool']}: {e['data'].get('error',{}).get('code','')}"))
        self.event_bus.subscribe("plan_complete", self._on_plan_complete)
        self.event_bus.subscribe("plan_failed", lambda ctx, e: sp(f"  [!] 规划失败"))
        self.event_bus.subscribe("execution_started", lambda ctx, e: sp(f"\n[履约] 开始预约..."))
        self.event_bus.subscribe("booking_status_changed", lambda ctx, e: sp(f"  . {e['data'].get('name','')}: {e['data'].get('status','')}"))
        self.event_bus.subscribe("execution_complete", lambda ctx, e: sp(f"\n[履约完成] 所有预订成功"))
        self.event_bus.subscribe("node_failed", lambda ctx, e: sp(f"  [!] {e['data'].get('name','')} 失败"))
        self.event_bus.subscribe("replan_ready", lambda ctx, e: sp(f"  [重规划] 调整方案已就绪"))
        self.event_bus.subscribe("resource_loss_warning", self._on_resource_loss)

    def _on_plan_complete(self, ctx, event):
        itin = event["data"].get("itinerary", {})
        nodes = itin.get("nodes", [])
        sp(f"\n[规划完成] 共 {len(nodes)} 个活动")
        for n in nodes:
            sp(f"  {n.get('scheduled_start','?')}-{n.get('scheduled_end','?')} {n.get('poi_name','')} ({n.get('duration_min',0)}min)")
        sp(f"  摘要: {itin.get('summary','')}")

    def _on_resource_loss(self, ctx, event):
        losses = event["data"].get("losses", [])
        if losses:
            sp(f"  [!] 取消将释放以下预约/资格：")
            for loss in losses:
                sp(f"      - {loss.get('name','')} ({loss.get('type',loss.get('reason',''))})")

    # ── Mock 意图路由（纯规则，不调 LLM） ──

    def _mock_route_action(self, user_input: str, state: str) -> tuple[str, str]:
        """纯规则意图路由，替代 LLM 的 chat_json 调用"""
        inp = user_input.strip()
        has_plan = state in ("pending_confirm", "executing", "completed")
        # replan
        if any(k in inp for k in ("换一个", "重新规划", "不喜欢", "太赶", "太早", "太晚")):
            return "replan", "好的，根据您的反馈重新调整方案"
        # show_plan
        if any(k in inp for k in ("行程", "方案在哪", "看看", "展示", "结果")):
            return ("show_plan", "这是当前方案") if has_plan else ("chat", "正在为您规划，稍等一下")
        # confirm
        if any(k in inp for k in ("好", "行", "确认", "安排", "可以", "就这样")):
            return ("confirm", "好的，马上安排预约") if has_plan else ("plan", "好的，开始规划")
        # cancel
        if any(k in inp for k in ("取消", "不去了", "算了")):
            return "cancel", "好的，已取消"
        # 新对话或 draft → 检查信息完整性
        if state in ("none", "unknown", "draft", "init", ""):
            has_time = any(k in inp for k in ("点", ":", "下午", "上午", "中午", "晚上"))
            has_people = any(k in inp for k in ("人", "朋友", "孩子", "老婆", "老公", "一起", "和"))
            if not has_time:
                return "clarify", "请问您打算几点出发呢？"
            if not has_people:
                return "clarify", "请问几个人去？"
            return "plan", "好的，开始搜索方案"
        return "chat", "您说什么？"

    # ── Agent 循环 ──

    async def start(self):
        self.session_id = ""
        sp("\n[Agent] 你好！今天下午有什么安排？")

    async def _build_context(self) -> str:
        """构建当前状态摘要（异步）"""
        if not self.session_id:
            return json.dumps({"state": "none", "plan": "无"}, ensure_ascii=False)
        try:
            s = await self.orchestrator.get_status(self.session_id)
            nodes_text = "、".join(
                f"{n.get('start_time','')} {n.get('name','')}" for n in s.nodes[:3]
            ) or "无"
            return json.dumps({"state": s.itinerary_state, "plan": nodes_text}, ensure_ascii=False)
        except Exception:
            return json.dumps({"state": "unknown", "plan": "无"}, ensure_ascii=False)

    async def handle_message(self, user_input: str) -> str:
        """LLM 驱动的 Agent 路由 — 替代硬编码状态机"""
        self.conversation_history.append({"role": "user", "content": user_input})

        # 取消直接处理（不经过 LLM，避免漏判）
        if any(k in user_input for k in ("取消", "再见")):
            if self.session_id:
                await self.orchestrator.cancel_session(self.session_id)
                self.session_id = ""
            reply = "好的，已取消。下次需要随时找我。"
            self.conversation_history.append({"role": "assistant", "content": reply})
            return reply

        # 首次输入 → 创建 session（不主动规划，等 LLM 决定 action）
        if not self.session_id:
            self.session_id = await self.orchestrator.start_session(user_input)

        # 意图理解（mock 模式用规则路由，不调 DeepSeek）
        ctx = await self._build_context()
        ctx_data = json.loads(ctx)
        state = ctx_data.get("state", "none")

        if self.mock_llm:
            action, response = self._mock_route_action(user_input, state)
        else:
            prompt = AGENT_PROMPT.format(state=state, plan_summary=ctx_data.get("plan", "无"))
            try:
                result = await self.llm.chat_json(
                    system=prompt,
                    messages=self.conversation_history[-8:],
                    temperature=0.2,
                )
            except Exception:
                return "我没理解，您能再说一遍吗？"
            action = result.get("action", "chat")
            response = result.get("response", "您说")

        self.conversation_history.append({"role": "assistant", "content": response})

        # 带 guardrail 的 action dispatch（state 已在上面获得）
        if state in ("none", "unknown", "draft") and action == "chat":
            action = "plan"
        invalid_map = {
            "confirm": ["init", "draft", "none", "cancelled"],
            "replan": ["init", "none", "cancelled"],
            "cancel": ["cancelled", "none"],
            "plan": ["init", "executing", "completed"],
            "show_plan": ["init", "none"],
        }
        if action in invalid_map and state in invalid_map[action]:
            action = "chat"

        # 执行（所有操作以 _plan_id 为唯一依据）
        if action == "plan":
            return await self._do_plan(user_input)
        elif action == "confirm":
            if not self._plan_id:
                return "方案尚未就绪，请稍后再确认"
            return await self._do_execute()
        elif action == "replan":
            self._plan_id = None
            return await self._do_replan(user_input)
        elif action == "show_plan":
            if not self._plan_id:
                return "正在为您规划行程，请稍等..."
            try:
                s = await self.orchestrator.get_status(self.session_id)
                return self._show_plan_str(s.nodes) if s.nodes else "方案已就绪"
            except Exception:
                return "正在为您规划行程，请稍等..."
        elif action == "cancel":
            await self.orchestrator.cancel_session(self.session_id)
            self.session_id = ""
            return "好的，已取消。"
        else:
            return response  # clarify 或 chat

    # ── 操作 ──

    async def _do_plan(self, user_input: str) -> str:
        # single-flight lock：已有规划任务则等待完成
        if self._planning:
            for _ in range(40):
                await asyncio.sleep(1)
                if self._plan_id:
                    break
            else:
                return "规划超时，请重试"
            s = await self.orchestrator.get_status(self.session_id)
            return self._show_plan_str(s.nodes) if s.nodes else "方案已就绪"

        self._planning = True
        self._plan_id = None

        # 保存当前 session_id 用于复用，保证 SSE 适配器过滤时能匹配
        existing_sid = self.session_id or ""
        if self.session_id:
            try:
                await self.orchestrator.cancel_session(self.session_id)
            except Exception:
                pass
            await asyncio.sleep(0.3)
        self.session_id = await self.orchestrator.start_session(user_input, session_id=existing_sid)

        # 等 Phase 1 完成
        for _ in range(40):
            await asyncio.sleep(1)
            s = await self.orchestrator.get_status(self.session_id)
            if s.itinerary_state in ("pending_confirm", "executing", "completed", "needs_replan"):
                self._plan_id = s.session_id + "_plan"
                self._planning = False
                return self._show_plan_str(s.nodes) if s.nodes else "方案已就绪"
        self._planning = False
        return "规划超时，请重试"

    async def _do_replan(self, user_input: str) -> str:
        sp("\n[重新规划] 根据您的反馈调整方案...")
        # 先读取当前方案摘要，拼到用户输入里，避免旧信息丢失
        old_plan = ""
        if self.session_id:
            try:
                s = await self.orchestrator.get_status(self.session_id)
                if s.nodes:
                    old_plan = "之前方案: " + "、".join(
                        f"{n.get('start_time','')} {n.get('name','')}" for n in s.nodes[:3]
                    )
            except Exception:
                pass
        enriched = f"{old_plan}。用户新要求: {user_input}" if old_plan else user_input
        return await self._do_plan(enriched)

    async def _do_execute(self) -> str:
        try:
            await self.orchestrator.confirm_itinerary(self.session_id)
        except Exception as e:
            return f"预约启动失败: {e}"
        sp("  预约进度：")
        for _ in range(20):
            await asyncio.sleep(1.5)
            s = await self.orchestrator.get_status(self.session_id)
            if s.itinerary_state in ("completed", "needs_replan"):
                break
        return "全部预约搞定！您按计划出发就行。"

    @staticmethod
    def _show_plan_str(nodes) -> str:
        if not nodes:
            return "暂无方案"
        return "\n".join(f"  {n.get('start_time','')} {n.get('name','')}" for n in nodes[:5])


# ── 主循环 ──────────────────────────────────────────────────

def main():
    use_teammate = "--teammate" in sys.argv

    sp("=" * 50)
    sp("  Meituan AI Agent - LLM Chat Mode")
    sp("=" * 50)

    agent = ChatAgent(use_teammate=use_teammate)
    asyncio.run(agent.start())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        while True:
            user_input = input("> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                sp("\n[Agent] 再见！")
                break
            sp("")
            response = loop.run_until_complete(agent.handle_message(user_input))
            sp(f"\n[Agent] {response}\n")
    except (KeyboardInterrupt, EOFError):
        sp("\n[Agent] 再见！")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
