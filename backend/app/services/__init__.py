"""Backend service layer for Anubhav-owned Module 4 flows."""

from app.services.agent_gateway import (
    AgentGateway,
    AgentTaskControlResult,
    OrchestratorAgentGateway,
    get_agent_gateway,
)
from app.services.auth_context import resolve_authenticated_user_id
from app.services.clarification_service import submit_clarification_answer
from app.services.conversation_service import list_task_messages, post_user_message
from app.services.goal_service import submit_goal
from app.services.sanitization import (
    sanitize_metadata_payload,
    sanitize_text_for_persistence,
)
from app.services.session_service import (
    InMemorySessionStore,
    SessionStore,
    close_session,
    create_session,
    get_session,
    get_session_store,
    resolve_portal_context,
    update_session,
)
from app.services.task_service import apply_task_action, list_user_tasks

__all__ = [
    "AgentGateway",
    "AgentTaskControlResult",
    "InMemorySessionStore",
    "SessionStore",
    "OrchestratorAgentGateway",
    "apply_task_action",
    "close_session",
    "create_session",
    "get_agent_gateway",
    "get_session",
    "get_session_store",
    "list_task_messages",
    "list_user_tasks",
    "post_user_message",
    "resolve_authenticated_user_id",
    "resolve_portal_context",
    "sanitize_metadata_payload",
    "sanitize_text_for_persistence",
    "submit_clarification_answer",
    "submit_goal",
    "update_session",
]
