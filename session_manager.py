"""
session_manager — 会话管理器

职责：
- 维护 agent_instances: Dict[str, ChatAgent]
- 同一 session_id 永远复用同一个 ChatAgent 实例
- 禁止在每次 request 中 new ChatAgent()
"""
from __future__ import annotations
from typing import Dict
from scripts.chat_demo import ChatAgent

agent_instances: Dict[str, ChatAgent] = {}

def get_or_create_agent(session_id: str) -> ChatAgent:
    if session_id not in agent_instances:
        agent = ChatAgent(session_id=session_id)
        agent_instances[session_id] = agent
    return agent_instances[session_id]

def destroy_agent(session_id: str) -> None:
    if session_id in agent_instances:
        del agent_instances[session_id]

def list_active_sessions() -> list:
    return list(agent_instances.keys())
