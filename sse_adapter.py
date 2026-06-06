"""
sse_adapter — EventBus → SSE projection

职责：
- 将 ChatAgent 的 EventBus 事件转成前端期望的 SSE 事件格式
- session 隔离：只转发匹配 session_id 的事件
"""
from __future__ import annotations

import asyncio
import json

from runtime.event_bus import EventBus

# ── SSE 事件映射表 ───────────────────────────────────────────
EVENT_MAP = {
    "plan_complete":      "itinerary_ready",
    "status_update":      "status",
    "booking_status_changed": "fulfill_item",
    "execution_complete": "fulfillment_complete",
    "execution_started":  "fulfill_init",
    "plan_failed":        "error",
    "replan_ready":       "status",
    "replan_applied":     "itinerary_updated",
    "monitor_started":    "text",
}

# 状态消息 → 前端 status card item id 的映射
STATUS_ID_MAP = [
    ("正在获取当前位置", 2, "loading"),
    ("正在骑行",        2, "loading"),
    ("已找到",          2, "done"),
    ("已找到",          3, "loading"),
    ("正在评分",        3, "loading"),
    ("正在生成行程方案", 4, "loading"),
    ("正在根据您的反馈", 4, "loading"),
    ("调整失败",        4, "done"),
    ("已保持原计划不变", 4, "done"),
]
CATEGORY_ICONS = {
    "main_activity":    "🎯",
    "indoor_playground":"🎯",
    "museum":           "🏛️",
    "exhibition":       "🎨",
    "restaurant":       "🍽️",
    "optional_activity":"🚶",
    "outdoor_walk":     "🚶",
    "shopping":         "🚶",
    "park":             "🌳",
    "entertainment":    "🎵",
    "transport":        "🚕",
    "rest":             "☕",
}

def _fmt_node(n: dict) -> dict:
    """将后端的节点数据格式转为前端 ItineraryCards 组件期望的格式"""
    category = n.get("category", "activity")
    return {
        "id": n.get("node_id", ""),
        "poiId": n.get("poi_id", ""),
        "name": n.get("poi_name", ""),
        "type": category,
        # NodeCard 读的是 timeStart / timeEnd（不是 startTime / endTime）
        "timeStart": n.get("scheduled_start", "") or n.get("start_time", ""),
        "timeEnd": n.get("scheduled_end", "") or n.get("end_time", ""),
        "duration": n.get("duration_min", 60),
        "status": n.get("status", "planned"),
        "tags": n.get("tags", []),
        # 补充字段让卡片正常渲染
        "icon": CATEGORY_ICONS.get(category, "📍"),
        "sub": n.get("address", ""),
        "reason": n.get("planner_reason", ""),
        "rating": n.get("rating", 0),
        "distance": f'{n.get("distance_km", 0):.1f}km' if n.get("distance_km") else "",
        "price": f'¥{n.get("ticket_price", n.get("avg_price", 0))}' if n.get("ticket_price", n.get("avg_price", 0)) else "",
        "user_pinned": n.get("user_pinned", False),
        "completed_lock": n.get("completed_lock", False),
        "locked": n.get("completed_lock", False) or n.get("soft_lock", False),
    }

def create_sse_session(event_bus: EventBus, session_id: str):
    """
    创建一个 session-scoped 的 SSE 事件队列。

    返回: (queue, unsubscribe_func)
    - queue: asyncio.Queue 从中读取事件
    - unsubscribe_func: 调用后停止监听
    """
    queue: asyncio.Queue = asyncio.Queue()

    def handler(ctx, event):
        # 只转发属于该 session 的事件
        evt_sid = event.get("session_id", "") or getattr(ctx, "session_id", "")
        if evt_sid != session_id:
            return

        # 映射事件类型
        etype = event.get("type", "")
        sse_type = EVENT_MAP.get(etype, etype)
        data = event.get("data", {})

        # 转换事件数据
        if sse_type == "itinerary_ready" and data.get("itinerary"):
            itin = data["itinerary"]
            nodes = itin.get("nodes", [])
            fmt_nodes = [_fmt_node(n) for n in nodes]
            # 转发可替换候选 POI
            alts_raw = data.get("alternatives", [])
            fmt_alts = [{
                "poiId": a.get("poi_id", ""),
                "name": a.get("name", ""),
                "category": a.get("category", ""),
                "icon": CATEGORY_ICONS.get(a.get("category", ""), "📍"),
                "sub": a.get("address", ""),
                "distanceKm": a.get("distance_km", 0),
                "rating": a.get("rating", 0),
                "queueText": f'排队{a.get("queue_time_min", 0)}分钟' if a.get("queue_time_min", 0) > 0 else "无需排队",
                "tags": a.get("tags", []),
            } for a in alts_raw if a.get("poi_id")]
            queue.put_nowait({
                "type": "itinerary_ready",
                "nodes": fmt_nodes,
                "summary": itin.get("summary", ""),
                "alternatives": fmt_alts,
            })
            return

        elif sse_type == "fulfill_item":
            queue.put_nowait({"type": "fulfill_item", "id": data.get("node_id", ""),
                "name": data.get("name", ""), "status": data.get("status", ""), "action": "reserve"})
            return

        elif sse_type == "status":
            msg = data.get("message", "")
            if msg:
                # 匹配 status card item id，让前端进度条能推进
                matched_id = None
                matched_status = None
                for keyword, sid, st in STATUS_ID_MAP:
                    if keyword in msg:
                        matched_id = sid
                        matched_status = st
                        break
                evt = {"type": "status", "text": msg}
                if matched_id:
                    evt["id"] = matched_id
                    evt["status"] = matched_status
                queue.put_nowait(evt)
            return

        elif sse_type == "error":
            queue.put_nowait({"type": "error", "message": f"规划失败: {data.get('error', '')}"})
            return

        elif sse_type == "itinerary_updated":
            itin2 = data.get("itinerary", {})
            nodes2 = itin2.get("nodes", [])
            fmt2 = [_fmt_node(n) for n in nodes2]
            queue.put_nowait({"type": "itinerary_updated", "nodes": fmt2})
            return

        # 兜底：直接转发
        queue.put_nowait({"type": sse_type, "data": data})

    event_bus.subscribe("*", handler)

    def cleanup():
        event_bus.unsubscribe("*", handler)

    return queue, cleanup
