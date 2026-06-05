"""
LLM Planner — 用 DeepSeek 替换所有 mock handler

职责:
1. 意图理解：解析用户自然语言 → 结构化场景/约束/偏好
2. POI 评分：基于用户偏好对候选 POI 做推理评分
3. 行程生成：编排时间线，生成可执行行程
4. 重规划：异常时保护 locked 节点，调整后续方案
5. 对话回复：管家身份的自然语言回复生成
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from core.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ─── System Prompts ──────────────────────────────────────────

BUTLER_SYSTEM = """你是本地生活管家「小美」，你的核心原则：

1. 身份定位：用户的生活助理，帮用户把"下午出去"的模糊想法变成可执行方案
2. 行为边界：
   - 不要只推荐，要推进规划、履约和异常恢复
   - 涉及取消/替换已预约节点，必须用户确认
   - 不伪造 API 事实——事实数据只能来自工具返回
   - 回复简洁，不用敬语，不用括号表情
3. 输出要求：
   - 严格输出 JSON
   - 禁止 Markdown
   - 字段缺失填 null

你的用户是普通市民，说话口语化，回复也要口语化。
"""

PREFERENCE_EXTRACTION_SYSTEM = """你是一个偏好提取器。从用户口语化的输入中提取结构化信息。

输出 JSON 格式：
{
    "scene": "family|friends|couple|solo",
    "start_time": "14:00" 或 null,
    "companions": ["adult_female", "child_age_5"] 或 [],
    "special_requirements": ["减肥", "清淡"] 或 [],
    "missing_info": ["start_time", "child_info"] 或 [],
    "preferences": {
        "indoor": true/false,
        "less_queue": true/false,
        "less_walk": true/false,
        "mode": "full_managed|light_managed"
    }
}

规则:
- 如果用户没提出发时间，missing_info 要包含 "start_time"
- 提到"老婆孩子在减肥" → special_requirements 加 "减肥" "清淡"
- 有儿童 → mode = full_managed，less_queue = true
- 朋友聚会 → mode = light_managed
- 不确定的字段填 null，不要猜测
"""

SCORING_SYSTEM = """你是一个冷静、克制的城市活动评分器。

根据用户场景和偏好，对候选 POI 进行损益评估。

【硬性约束】
- 只对你收到的候选列表中的 POI 进行评分
- 严禁编造不在列表中的 POI 或评分
- 每个 scored 条目的 poi_id 必须来自输入的 candidates 列表

输出 JSON 格式：
{
    "scored": [
        {
            "poi_id": "string",
            "score": 0-100,
            "score_detail": {
                "distance_score": 0-30,
                "queue_penalty": 0-30,
                "preference_match": 0-20,
                "comfort_score": 0-15,
                "mode_adjustment": 0-5
            },
            "planner_reason": "一句话理由，不超过20字",
            "recommended": true/false
        }
    ]
}

评分规则:
- 家庭场景：child_friendly 优先，排队超过 30min 扣 20 分
- 朋友场景：氛围感标签优先，评分权重高
- 特殊需求（减肥/清淡）：healthy_option 匹配加分
- 距离超过 5km 扣分
"""

ITINERARY_GENERATION_SYSTEM = """你是一个行程编排师。根据选中的 POI 和用户约束，生成一个时间合理的 4-6 小时下午行程。

【绝对规则——必须遵守】
1. 节点数: 必须 3 个（main_activity + restaurant + optional_activity），缺少无效
2. POI 合法性: 所有 poi_id 必须来自 valid_poi_ids，严禁编造
3. 时间窗:
   - 午餐(11:30-13:30) 晚餐(17:30-19:00)
   - 主活动 60-120min，餐厅 55-70min，轻活动 30-45min
   - 相邻节点缓冲 10-15min
   - 含儿童 20:00 前结束
4. 输出格式:
```json
{"summary":"","total_duration_min":0,"nodes":[{"node_id":"","poi_id":"","poi_name":"","category":"","start_time":"","end_time":"","duration_min":0,"tags":[],"feasibility_note":""}]}
```

【反馈处理——如果 user_feedback 不为空，必须强制改变时间分配】
用户说「太早」→ 对应节点时间推迟 ≥ 60 分钟
用户说「太晚」→ 对应节点时间提前 ≥ 60 分钟
用户说「太赶」→ 缓冲时间增加到 20 分钟
用户说「太松」→ 减少到 5 分钟
如果新方案和旧方案时间完全相同，视为无效输出，必须重新生成。
"""

REPLAN_SYSTEM = """你是行程重规划器。必须全量重算行程，禁止局部修改。

【强制规则】
1. 丢弃旧方案所有时间分配，重新计算每个节点的时间
2. user_feedback 是硬性约束：
   - "太早" → 对应节点推迟 ≥ 60min
   - "太晚" → 对应节点提前 ≥ 60min
   - "太赶" → 缓冲加到 20min
3. 保护 completed_lock 和 user_pinned 节点

输出 JSON：
{"need_user_confirm":true,"replan_summary":"","changed_nodes":[{"old_node_id":"","new_poi_id":"","new_name":"","new_scheduled_time":"","reason":""}],"unchanged_nodes":[]}
"""

RESPONSE_SYSTEM = """你是一个本地生活管家。

根据当前系统状态，用口语化的方式回复用户。

规则:
- 简短，不超过 3 句话
- 不用敬语，不用表情符号
- 不用 Markdown
- 直接说事，别铺垫
"""


class LLMPlanner:
    """
    LLM 规划器 — 使用 DeepSeek 执行规划推理。

    所有方法返回格式与原来的 mock handler 完全一致，
    可以直接注册到 ToolRegistry。
    """

    def __init__(self, llm: LLMClient):
        self.llm = llm

    # ── 意图理解 ──

    async def handle_user_context(self, payload: dict) -> dict:
        """解析用户输入 → 结构化上下文"""
        user_input = payload.get("input", "")
        try:
            result = await self.llm.chat_json(
                system=PREFERENCE_EXTRACTION_SYSTEM,
                messages=[{"role": "user", "content": user_input}],
            )
            scene = result.get("scene")
            if scene not in ("family", "friends", "couple", "solo"):
                scene = "family"
            mode = result.get("preferences", {}).get("mode")
            if mode not in ("full_managed", "light_managed"):
                mode = "full_managed"
            return {"success": True, "data": {
                "scene": scene,
                "time_range": "afternoon",
                "distance_constraint": "nearby",
                "companions_text": user_input,
                "special_requirements": result.get("special_requirements", []),
                "missing_info": result.get("missing_info", []),
                "intent_conflict": False,
                "mode": mode,
                "start_time": result.get("start_time", "14:00"),
            }}
        except Exception as e:
            logger.error("[LLMPlanner] user_context 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    # ── POI 评分 ──

    async def handle_candidates_score(self, payload: dict) -> dict:
        """对候选 POI 进行 LLM 推理评分 + POI 合法性校验"""
        candidates = payload.get("candidates", [])
        user_context = payload.get("user_context", {})
        weather = payload.get("weather", {})

        # 收集合法 POI ID
        valid_ids = {c.get("poi_id") for c in candidates if c.get("poi_id")}

        try:
            result = await self.llm.chat_json(
                system=SCORING_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({
                    "candidates": candidates,
                    "user_scenario": user_context.get("scene", "family"),
                    "special_requirements": user_context.get("special_requirements", []),
                    "weather": weather.get("condition", "晴"),
                    "temperature": weather.get("temperature_c", 25),
                }, ensure_ascii=False)}],
            )
            # 过滤掉 LLM 编造的 POI 评分
            scored = result.get("scored", [])
            validated = [s for s in scored if s.get("poi_id") in valid_ids]
            if len(validated) < len(scored):
                logger.warning("[LLMPlanner] 过滤 %d 个编造 POI 评分",
                               len(scored) - len(validated))
            return {"success": True, "data": {"scored": validated}}
        except Exception as e:
            logger.error("[LLMPlanner] scoring 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    # ── 行程生成 ──

    async def handle_itinerary_generate(self, payload: dict) -> dict:
        """编排行程 + POI 合法性校验"""
        selected = payload.get("selected_nodes", {})
        departure = payload.get("departure_time", "14:00")
        scene = payload.get("scene", "family")
        user_feedback = payload.get("user_feedback", "")

        # 收集所有合法的 POI ID + 类别映射
        valid_poi_ids = set()
        poi_category_map = {}  # poi_id → expected category
        for key in ("main_activity", "restaurant", "optional_activity"):
            node = selected.get(key, {})
            if isinstance(node, dict) and node.get("poi_id"):
                pid = node["poi_id"]
                valid_poi_ids.add(pid)
                poi_category_map[pid] = key  # main_activity → "main_activity"

        try:
            result = await self.llm.chat_json(
                system=ITINERARY_GENERATION_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({
                    "departure_time": departure,
                    "scene": scene,
                    "valid_poi_ids": list(valid_poi_ids),
                    "main_activity": selected.get("main_activity", {}),
                    "restaurant": selected.get("restaurant", {}),
                    "optional_activity": selected.get("optional_activity", {}),
                    "user_feedback": user_feedback,
                }, ensure_ascii=False)}],
            )
            # POI 合法性校验：先按严格模式（ID+类别），再按宽松模式（仅ID）
            raw_nodes = result.get("nodes", [])
            validated_nodes = []
            strict_fail = []
            for node in raw_nodes:
                poi_id = node.get("poi_id", "")
                category = node.get("category", "")
                node_name = node.get("poi_name", "")
                if poi_id not in valid_poi_ids:
                    logger.warning("[LLMPlanner] 过滤编造 POI: %s (%s)", node_name, poi_id)
                    continue
                expected_cat = poi_category_map.get(poi_id, "")
                if expected_cat and category and expected_cat != category:
                    strict_fail.append(node)
                    continue
                validated_nodes.append(node)
            # 严格模式过滤后节点不足 2 个时，降级为仅 ID 校验
            if len(validated_nodes) < 2 and strict_fail:
                logger.warning("[LLMPlanner] 严格过滤后节点不足，降级为仅 ID 校验")
                validated_nodes = [n for n in raw_nodes if n.get("poi_id") in valid_poi_ids]

            # 确保有 restaurant 节点。如 LLM 未生成，从 selected 中补入
            has_restaurant = any(n.get("category") == "restaurant" for n in validated_nodes)
            if not has_restaurant:
                rest_data = selected.get("restaurant", {})
                if rest_data and rest_data.get("poi_id"):
                    logger.warning("[LLMPlanner] LLM 未生成餐厅节点，自动补入")
                    validated_nodes.append({
                        "node_id": f"node_{len(validated_nodes)+1:03d}",
                        "poi_id": rest_data.get("poi_id", ""),
                        "poi_name": rest_data.get("name", "餐厅"),
                        "category": "restaurant",
                        "start_time": "",
                        "end_time": "",
                        "duration_min": 60,
                        "tags": [],
                    })

            import uuid
            return {"success": True, "data": {
                "itinerary_id": f"iti_{uuid.uuid4().hex[:8]}",
                "nodes": validated_nodes,
                "total_duration_min": result.get("total_duration_min", 180),
                "summary": result.get("summary", ""),
            }}
        except Exception as e:
            logger.error("[LLMPlanner] generate 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    # ── 重规划 ──

    async def handle_itinerary_replan(self, payload: dict) -> dict:
        """局部重规划（约束驱动）"""
        try:
            trigger = payload.get("trigger", {})
            user_feedback = payload.get("user_feedback", trigger.get("description", ""))
            result = await self.llm.chat_json(
                system=REPLAN_SYSTEM,
                messages=[{"role": "user", "content": json.dumps({
                    "trigger_reason": trigger.get("type", "unknown"),
                    "user_feedback": user_feedback,
                    "locked_nodes": payload.get("policy", {}).get("locked_nodes", []),
                    "pending_nodes": payload.get("policy", {}).get("pending_nodes", []),
                }, ensure_ascii=False)}],
            )
            import uuid
            return {"success": True, "data": {
                "replan_id": f"rep_{uuid.uuid4().hex[:8]}",
                "need_user_confirm": result.get("need_user_confirm", True),
                "changed_nodes": result.get("changed_nodes", []),
                "unchanged_nodes": result.get("unchanged_nodes", []),
                "updated_route_required": True,
            }}
        except Exception as e:
            logger.error("[LLMPlanner] replan 失败: %s", e)
            return {"success": False, "error": {"code": "llm_error", "message": str(e)}}

    # ── 对话回复 ──

    async def generate_response(self, system_state: dict, user_message: str) -> str:
        """生成管家回复"""
        try:
            text = await self.llm.chat(
                system=RESPONSE_SYSTEM,
                messages=[
                    {"role": "user", "content": json.dumps({
                        "system_state": system_state,
                        "user_said": user_message,
                    }, ensure_ascii=False)},
                ],
            )
            return text
        except Exception as e:
            return f"好的，我看看（系统处理中）"
