"""
LLM Planner — 融合 V2 完整功能（LLM + 规则兜底）
- clarify_needs / confirm_preferences / score_candidates
- plan_itinerary / replan_partial / simulate / queue_advice
- 每个 LLM 函数有对应的 _fallback_*() 规则兜底
"""
from __future__ import annotations
import json, logging, uuid, re, random
from typing import Optional, Any
from agent.llm_client import LLMClient
from planner.prompts import *

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════

def _parse_hhmm(value: str) -> Optional[int]:
    """HH:MM → 分钟数"""
    try:
        h, m = map(int, str(value).strip().split(":"))
        return h * 60 + m
    except Exception:
        return None

def _fmt_hhmm(total_minutes: int) -> str:
    total_minutes = max(0, int(total_minutes))
    return f"{total_minutes // 60 % 24:02d}:{total_minutes % 60:02d}"

def _parse_cn_number(value: str) -> Optional[int]:
    """中文数字/阿拉伯数字 → int"""
    cn = {"零":0,"一":1,"二":2,"两":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
    v = str(value).strip()
    if v.isdigit():
        return int(v)
    if v in cn:
        return cn[v]
    return None

def _default_start_time_from_current(current_time: str) -> str:
    """根据当前时间返回合理的出发时间"""
    now = _parse_hhmm(current_time) or 14 * 60
    if now < 10 * 60:
        return "14:00"
    if now < 13 * 60:
        return "14:00"
    return _fmt_hhmm(now + 60)


# ═══════════════════════════════════════════════════════════════════
# CLARIFY — 深度追问（LLM + 规则兜底）
# ═══════════════════════════════════════════════════════════════════

async def clarify_needs(llm: LLMClient, message: str,
                        current_time: str = "14:00",
                        location_hint: str = "") -> dict:
    """LLM 追问——深度分析同行人画像"""
    if not location_hint:
        location_hint = "位置：北京望京"
    try:
        result = await llm.chat_json(
            system=CLARIFY_SYSTEM,
            messages=[{"role": "user", "content": CLARIFY_USER.format(
                message=message, current_time=current_time,
                location_hint=location_hint,
            )}],
        )
        inferred = result.get("inferred", {})
        # 补全缺失字段
        inferred.setdefault("start_time",
            _default_start_time_from_current(current_time))
        return {"success": True, "data": {
            "inferred": inferred,
            "confirm_message": result.get("confirm_message", ""),
            "clarify_questions": result.get("clarify_questions", []),
        }}
    except Exception as e:
        logger.warning("[Clarify] LLM 失败，用规则兜底: %s", e)
        return _fallback_clarify(message, current_time, location_hint)


def _fallback_clarify(message: str, current_time: str,
                      location_hint: str = "") -> dict:
    """规则兜底——关键词匹配提取"""
    msg = message.lower()
    inferred = {
        "scenario": "solo", "start_time": _default_start_time_from_current(current_time),
        "duration_hours": 3, "companions_desc": "独自一人",
        "has_children": False, "child_age": None, "child_purpose": None,
        "has_elderly": False, "elderly_no_walking": None,
        "group_gender": "unknown", "male_count": 0, "female_count": 0,
        "friends_activity_type": None,
        "female_weight_loss": False, "female_prefer_low_intensity": None,
        "male_prefer_high_intensity": None, "all_adults_confirmed": True,
    }
    # 场景检测
    if any(k in msg for k in ("孩子", "小孩", "儿童", "宝宝", "娃")):
        inferred["scenario"] = "family"
        inferred["has_children"] = True
        inferred["companions_desc"] = "带孩子的家庭"
        # 尝试提取年龄
        age_m = re.search(r"(\d+)[岁]", msg)
        if age_m:
            inferred["child_age"] = int(age_m.group(1))
    elif any(k in msg for k in ("朋友", "兄弟", "闺蜜", "同事")):
        inferred["scenario"] = "friends"
        inferred["companions_desc"] = "和朋友一起"
    elif any(k in msg for k in ("老婆", "老公", "媳妇", "男朋友", "女朋友")):
        inferred["scenario"] = "couple"
        inferred["companions_desc"] = "二人世界"
    # 时间提取
    tm = re.search(r"(\d+)[:：点](\d*)|(\d+)[:：点]?", msg)
    if tm:
        h = int(tm.group(1) or tm.group(3) or "14")
        m = int(tm.group(2)) if tm.group(2) else 0
        if h < 7:
            h += 12
        inferred["start_time"] = f"{h:02d}:{m:02d}"
    # 减肥/清淡检测
    if any(k in msg for k in ("减肥", "瘦", "减脂")):
        inferred["female_weight_loss"] = True
    # 生成追问
    questions = []
    if inferred["has_children"] and inferred["child_age"] is None:
        questions.append("孩子几岁了？")
    if inferred["scenario"] == "friends":
        questions.append("男生女生各几个？大家喜欢什么类型的活动？")
    return {"success": True, "data": {
        "inferred": inferred,
        "confirm_message": f"了解到您是{inferred['companions_desc']}，{_desc_time(inferred)}",
        "clarify_questions": questions[:3],
    }}

def _desc_time(inferred: dict) -> str:
    t = inferred.get("start_time", "14:00")
    return f"大概{t}出发" if t else "想几点出发呢"


# ═══════════════════════════════════════════════════════════════════
# CONFIRM_PREFS — 确认提取的偏好
# ═══════════════════════════════════════════════════════════════════

async def confirm_preferences(llm: LLMClient, inferred: dict,
                              user_response: str,
                              history: list[dict] = None) -> dict:
    """LLM 确认偏好——用户回应后更新推断"""
    history_section = ""
    if history:
        history_section = "对话历史：\n" + "\n".join(
            f"{m.get('role','')}: {m.get('content','')}" for m in history[-4:]
        ) + "\n\n"
    try:
        result = await llm.chat_json(
            system=CONFIRM_PREFS_SYSTEM,
            messages=[{"role": "user", "content": CONFIRM_PREFS_USER.format(
                history_section=history_section,
                inferred_json=json.dumps(inferred, ensure_ascii=False),
                user_response=user_response,
            )}],
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.warning("[ConfirmPrefs] LLM 失败，用规则兜底: %s", e)
        return _fallback_confirm_prefs(inferred, user_response)


def _fallback_confirm_prefs(inferred: dict, user_response: str) -> dict:
    """规则兜底——合并用户回答到推断中"""
    merged = dict(inferred)
    resp = user_response.lower()
    # 时间更新
    tm = re.search(r"(\d+)[:：点](\d*)", resp)
    if tm:
        h = int(tm.group(1))
        m = int(tm.group(2)) if tm.group(2) else 0
        merged["start_time"] = f"{h:02d}:{m:02d}"
    # 年龄更新
    age_m = re.search(r"(\d+)[岁]", resp)
    if age_m:
        merged["child_age"] = int(age_m.group(1))
    # 场景更新
    if any(k in resp for k in ("孩子", "小孩")):
        merged["has_children"] = True
        merged["scenario"] = "family"
    # 闺蜜/兄弟
    if any(k in resp for k in ("闺蜜", "姐妹")):
        merged["female_count"] = max(merged.get("female_count", 0), 2)
    if any(k in resp for k in ("兄弟", "哥们")):
        merged["male_count"] = max(merged.get("male_count", 0), 2)
    return {"success": True, "data": {
        "inferred": merged,
        "confirm_message": "好的，已更新信息",
        "all_confirmed": True,
    }}


# ═══════════════════════════════════════════════════════════════════
# SCORE_CANDIDATES — POI 评分（LLM + 规则兜底）
# ═══════════════════════════════════════════════════════════════════

async def score_candidates(llm: LLMClient, activities: list,
                           restaurants: list, session_facts: dict) -> dict:
    """LLM 评分——按场景分支打分"""
    scenario = session_facts.get("scene", "family")
    requirements = session_facts.get("special_requirements", [])
    weather_text = session_facts.get("weather", "晴")
    candidate_list = json.dumps(
        [{"poi_id": c["poi_id"], "name": c["name"],
          "category": c.get("category", ""),
          "tags": c.get("tags", []), "rating": c.get("rating", 0),
          "distance_km": c.get("distance_km", 0),
          "avg_price": c.get("avg_price", 0),
          "estimated_duration_min": c.get("estimated_duration_min", 60)}
         for c in (activities + restaurants)[:30]],
        ensure_ascii=False,
    )
    try:
        result = await llm.chat_json(
            system=SCORING_SYSTEM,
            messages=[{"role": "user", "content": SCORING_USER.format(
                scenario=scenario,
                requirements="、".join(requirements) if requirements else "无",
                weather_text=weather_text,
                candidate_list=candidate_list,
            )}],
        )
        return {"success": True, "data": {"scored": result.get("scored", [])}}
    except Exception as e:
        logger.warning("[Score] LLM 失败，用规则打分: %s", e)
        return _fallback_attach_scores(activities, restaurants, session_facts)


def _fallback_attach_scores(activities: list, restaurants: list,
                            session_facts: dict) -> dict:
    """规则兜底——按属性打分"""
    scenario = session_facts.get("scene", "family")
    scored = []
    for c in (activities + restaurants):
        s = 50
        tags_str = " ".join(c.get("tags", []))
        if scenario == "family":
            if c.get("child_friendly") or "亲子" in tags_str:
                s += 25
            if "室内" in tags_str:
                s += 15
        else:
            if any(t in tags_str for t in ("社交", "氛围", "拍照")):
                s += 15
        q = c.get("queue_time_min", 0)
        if q > 60:
            s = 0
        elif q > 30:
            s -= 25
        d = c.get("distance_km", 0)
        if d > 10:
            s -= 25
        elif d > 5:
            s -= 10
        if any(t in tags_str for t in ("低卡", "清淡", "健康")):
            s += 10
        scored.append({
            "poi_id": c.get("poi_id", ""),
            "score": max(0, min(100, s)),
            "tags_matched": [],
            "planner_reason": f"评分{s}",
            "recommended": s >= 70,
            "disqualified_reason": "排队过长" if q > 60 else None,
        })
    scored.sort(key=lambda s: s["score"], reverse=True)
    return {"success": True, "data": {"scored": scored}}


# ═══════════════════════════════════════════════════════════════════
# PLAN_ITINERARY — 行程规划（LLM + 规则兜底）
# ═══════════════════════════════════════════════════════════════════

async def plan_itinerary(llm: LLMClient, activities: list, restaurants: list,
                         weather: dict, session_facts: dict,
                         preferences: dict = None) -> dict:
    """LLM 规划——编排完整时间线"""
    departure = session_facts.get("start_time", "14:00")
    scenario = session_facts.get("scene", "family")
    companions_desc = session_facts.get("companions_desc", "")
    requirements = session_facts.get("special_requirements", [])

    def _fmt_list(items):
        return "\n".join(
            f"  - {c.get('name','')}({c.get('category','')}) "
            f"{c.get('address','')} | {c.get('tags',[])}"
            for c in items[:20]
        )

    weather_text = f"{weather.get('weather','晴')}，{weather.get('temperature',25)}℃"
    try:
        result = await llm.chat_json(
            system=PLANNER_SYSTEM,
            messages=[{"role": "user", "content": PLANNER_USER.format(
                departure_time=departure, scenario=scenario,
                companions_desc=companions_desc or "无",
                requirements="、".join(requirements) if requirements else "无",
                activities_text=_fmt_list(activities),
                restaurants_text=_fmt_list(restaurants),
                weather_text=weather_text,
            )}],
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.warning("[Plan] LLM 失败，用规则规划: %s", e)
        return _fallback_plan(activities, restaurants, session_facts)


def _fallback_plan(activities: list, restaurants: list,
                   session_facts: dict) -> dict:
    """规则兜底——按固定算法编排"""
    departure = session_facts.get("start_time", "14:00")
    has_child = session_facts.get("has_children", False) or \
                any(k in str(session_facts.get("companions_desc", ""))
                    for k in ("孩子", "小孩"))
    # 排序
    acts = sorted(activities, key=lambda a: (
        a.get("rating", 0), -a.get("distance_km", 99)), reverse=True)
    rests = sorted(restaurants, key=lambda r: (
        r.get("rating", 0), -r.get("distance_km", 99)), reverse=True)
    best_act = acts[0] if acts else None
    best_rest = rests[0] if rests else None
    if not best_act and not best_rest:
        return {"success": False, "error": {"code": "no_candidates"}}
    dh, dm = map(int, departure.split(":") if ":" in departure else ("14", "00"))
    dep = dh * 60 + dm
    buf = 15
    nodes = []

    def add_node(src, cat, start):
        dur = min(src.get("estimated_duration_min", 90), 90 if has_child else 999)
        return {
            "node_id": f"n_{len(nodes)+1}", "poi_id": src.get("poi_id", ""),
            "poi_name": src.get("name", ""), "category": cat,
            "start_time": _fmt_hhmm(start),
            "end_time": _fmt_hhmm(start + dur),
            "duration_min": dur, "buffer_before_min": buf,
            "tags": src.get("tags", []), "feasibility_note": "",
        }

    cur = dep + buf
    meal_mode = "skip"
    # 用餐模式
    if dep >= 13 * 60:
        meal_mode = "dinner"
        rest_window = (17 * 60 + 30, 19 * 60 + 30)
    elif dep <= 11 * 60:
        meal_mode = "lunch"
        rest_window = (11 * 60 + 30, 13 * 60)
    else:
        meal_mode = "lunch_mid"
        rest_window = (12 * 60 + 30, 13 * 60 + 30)

    if best_act:
        n = add_node(best_act, "main_activity", cur)
        nodes.append(n)
        cur = _parse_hhmm(n["end_time"]) + buf
    if meal_mode == "dinner" and best_rest:
        r_start = max(rest_window[0], cur + buf)
        if r_start + 60 <= rest_window[1]:
            nodes.append(add_node(best_rest, "restaurant", r_start))
            cur = r_start + 60 + buf
    elif best_rest:
        r_start = max(cur, rest_window[0])
        nodes.append(add_node(best_rest, "restaurant", r_start))
        cur = r_start + 60 + buf
    if len(nodes) < 3 and len(acts) > 1:
        n = add_node(acts[1], "optional_activity", cur)
        nodes.append(n)

    total = sum(n["duration_min"] for n in nodes) if nodes else 0
    return {"success": True, "data": {
        "summary": " → ".join(n["poi_name"] for n in nodes[:3]) + f"，共{total}分钟",
        "total_duration_min": total, "meal_mode": meal_mode, "nodes": nodes,
    }}


# ═══════════════════════════════════════════════════════════════════
# REPLAN — 重规划（LLM + 规则兜底）
# ═══════════════════════════════════════════════════════════════════

async def replan_partial(llm: LLMClient, event: dict, itinerary: list,
                         alternatives: dict, memory: dict = None) -> dict:
    """LLM 重规划"""
    def _fmt_itin(nodes):
        return "\n".join(
            f"  {n.get('start_time','')}-{n.get('end_time','')} "
            f"{n.get('poi_name','')} [{n.get('status','planned')}]"
            for n in nodes[:10]
        )
    try:
        result = await llm.chat_json(
            system=REPLANNER_SYSTEM,
            messages=[{"role": "user", "content": REPLANNER_USER.format(
                event_type=event.get("type", "unknown"),
                event_description=event.get("description", ""),
                affected_node_id=event.get("affected_node_id", ""),
                itinerary_text=_fmt_itin(itinerary),
                alternatives_text=json.dumps(alternatives, ensure_ascii=False)[:1000],
            )}],
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.warning("[Replan] LLM 失败，用规则兜底: %s", e)
        return _fallback_replan(event, alternatives)


def _fallback_replan(event: dict, alternatives: dict) -> dict:
    """规则兜底——简单替换触发节点"""
    affected = event.get("affected_node_id", "")
    alt_list = alternatives.get(affected, [])
    changed = []
    if alt_list and len(alt_list) > 0:
        alt = alt_list[0]
        changed.append({
            "old_node_id": affected,
            "new_poi_id": alt.get("poi_id", ""),
            "new_name": alt.get("name", ""),
            "new_scheduled_time": alt.get("start_time", ""),
        })
    return {"success": True, "data": {
        "replan_summary": f"替换了{len(changed)}个活动",
        "meal_mode": "skip", "need_user_confirm": True,
        "nodes": changed,
    }}


# ═══════════════════════════════════════════════════════════════════
# SIMULATOR — 环境模拟器
# ═══════════════════════════════════════════════════════════════════

async def generate_simulator_event(llm: LLMClient,
                                   session_context: dict) -> dict:
    """LLM 生成模拟事件"""
    context_text = json.dumps(session_context, ensure_ascii=False)[:500]
    itinerary_text = "\n".join(
        f"  {n.get('start_time','')} {n.get('poi_name','')}"
        for n in (session_context.get("itinerary", []) or [])[:5]
    ) or "无"
    try:
        result = await llm.chat_json(
            system=SIMULATOR_SYSTEM,
            messages=[{"role": "user", "content": SIMULATOR_USER.format(
                context_text=context_text,
                itinerary_text=itinerary_text,
                count=1,
            )}],
        )
        return {"success": True, "data": result.get("events", [])}
    except Exception:
        return _fallback_simulator_event(session_context)


def _fallback_simulator_event(context: dict) -> dict:
    """规则兜底——随机返回预设事件"""
    events = [
        {"event_type": "queue_spike", "target_poi_id": "rest_family_001",
         "severity": "warning", "message": "餐厅排队突然增加到45分钟",
         "delay_seconds": 0},
        {"event_type": "node_checkin", "target_poi_id": None,
         "severity": "info", "message": "您已到达第一个活动地点",
         "delay_seconds": 5},
        {"event_type": "weather_change", "target_poi_id": None,
         "severity": "info", "message": "当前天气转为多云，适合户外活动",
         "delay_seconds": 10},
    ]
    return {"success": True, "data": [random.choice(events)]}


# ═══════════════════════════════════════════════════════════════════
# QUEUE_ADVICE — 排队建议
# ═══════════════════════════════════════════════════════════════════

async def get_queue_advice(llm: LLMClient, restaurant_name: str,
                           current_wait: int, trend: str = "stable",
                           has_children: bool = False,
                           scenario: str = "family") -> dict:
    """LLM 排队建议"""
    try:
        result = await llm.chat_json(
            system=QUEUE_ADVICE_SYSTEM,
            messages=[{"role": "user", "content": QUEUE_ADVICE_USER.format(
                restaurant_name=restaurant_name, current_wait=current_wait,
                trend=trend, has_children=str(has_children).lower(),
                scenario=scenario,
            )}],
        )
        return {"success": True, "data": result}
    except Exception:
        return _fallback_queue_advice(restaurant_name, current_wait, trend)


def _fallback_queue_advice(name: str, wait: int, trend: str) -> dict:
    """规则兜底"""
    if wait > 45:
        return {"advice": "换一家", "reason": "排队太久", "estimated_remaining": wait}
    if wait > 30 and trend == "increasing":
        return {"advice": "先去别处", "reason": "排队还在涨", "estimated_remaining": wait}
    return {"advice": "取号等", "reason": f"约{wait}分钟", "estimated_remaining": max(0, wait - 5)}


# ═══════════════════════════════════════════════════════════════════
# FEASIBILITY — 可行性校验
# ═══════════════════════════════════════════════════════════════════

def validate_feasibility(nodes: list[dict], session_facts: dict) -> list[str]:
    """校验行程可行性，返回风险列表"""
    risks = []
    if not nodes:
        return ["行程为空"]
    has_child = session_facts.get("has_children", False) or \
                any(k in str(session_facts.get("companions_desc", ""))
                    for k in ("孩子", "小孩"))
    # 时间重叠检查
    for i in range(len(nodes) - 1):
        c, nxt = nodes[i], nodes[i + 1]
        if c.get("end_time") and nxt.get("start_time"):
            if c["end_time"] >= nxt["start_time"]:
                risks.append(f"「{c.get('poi_name','')}」与「{nxt.get('poi_name','')}」时间重叠")
    # 儿童约束
    if has_child:
        for n in nodes:
            dur = n.get("duration_min", 0)
            if dur > 90:
                risks.append(f"「{n.get('poi_name','')}」时长{dur}min超过儿童上限90min")
    return risks


# ═══════════════════════════════════════════════════════════════════
# LLMPlanner 类（V4 原有 Tool Handlers + 新增 V2 函数）
# ═══════════════════════════════════════════════════════════════════

class LLMPlanner:
    """集成 V2 LLM 函数 + V4 Tool Handlers"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    # ── V2 风格函数 ──

    async def clarify_needs(self, message: str, current_time: str = "14:00",
                             location_hint: str = "") -> dict:
        return await clarify_needs(self.llm, message, current_time, location_hint)

    async def confirm_preferences(self, inferred: dict, user_response: str,
                                   history: list[dict] = None) -> dict:
        return await confirm_preferences(self.llm, inferred, user_response, history)

    async def score_candidates(self, activities: list, restaurants: list,
                                session_facts: dict) -> dict:
        return await score_candidates(self.llm, activities, restaurants, session_facts)

    async def plan_itinerary(self, activities: list, restaurants: list,
                              weather: dict, session_facts: dict,
                              preferences: dict = None) -> dict:
        return await plan_itinerary(self.llm, activities, restaurants,
                                    weather, session_facts, preferences)

    async def replan_partial(self, event: dict, itinerary: list,
                              alternatives: dict, memory: dict = None) -> dict:
        return await replan_partial(self.llm, event, itinerary, alternatives, memory)

    async def generate_simulator_event(self, session_context: dict) -> dict:
        return await generate_simulator_event(self.llm, session_context)

    async def get_queue_advice(self, restaurant_name: str, current_wait: int,
                                trend: str = "stable", has_children: bool = False,
                                scenario: str = "family") -> dict:
        return await get_queue_advice(self.llm, restaurant_name, current_wait,
                                       trend, has_children, scenario)

    def validate_feasibility(self, nodes: list[dict], session_facts: dict) -> list[str]:
        return validate_feasibility(nodes, session_facts)

    # ── V4 原有 Tool Handlers（保持不变，作为工具注册入口）──

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
                "intent_conflict": False, "mode": mode,
                "start_time": result.get("start_time", "14:00"),
            }}
        except Exception as e:
            logger.error("[LLMPlanner] user_context 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    async def handle_candidates_score(self, payload: dict) -> dict:
        candidates = payload.get("candidates", [])
        user_context = payload.get("user_context", {})
        acts = [c for c in candidates if c.get("category", "") != "restaurant"]
        rests = [c for c in candidates if c.get("category", "") == "restaurant"]
        result = await self.score_candidates(acts, rests, user_context)
        if result.get("success"):
            return result
        return {"success": False, "error": {"code": "llm_error"}}

    async def handle_itinerary_generate(self, payload: dict) -> dict:
        selected = payload.get("selected_nodes", {})
        departure = payload.get("departure_time", "14:00")
        session_facts = {
            "start_time": departure,
            "scene": payload.get("scene", "family"),
            "special_requirements": [],
            "companions_desc": payload.get("user_feedback", ""),
        }
        acts = [v for v in selected.values() if v.get("category") != "restaurant"]
        rests = [v for v in selected.values() if v.get("category") == "restaurant"]
        result = await self.plan_itinerary(acts, rests, {}, session_facts)
        if result.get("success"):
            return result
        return {"success": False, "error": {"code": "llm_error"}}

    async def handle_itinerary_replan(self, payload: dict) -> dict:
        trigger = payload.get("trigger", {})
        alt_data = {payload.get("policy", {}).get("locked_nodes", [None])[0]: []}
        result = await self.replan_partial(trigger, [], alt_data)
        return result

    async def generate_response(self, state: str, user_message: str) -> str:
        try:
            return await self.llm.chat(system=RESPONSE_SYSTEM,
                messages=[{"role":"user","content":json.dumps(
                    {"state":state,"user_said":user_message},
                    ensure_ascii=False)}])
        except Exception:
            return "好的，我看看（系统处理中）"
