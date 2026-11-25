# Module 4 Proof-of-Concept Assumptions & Contracts

This document records the engineering assumptions implemented in Anubhav-owned Module 4 files (`backend/app/schemas/`, `backend/app/services/`, and `backend/app/api/`) without modifying Ayush's, Madhav's, or Shubh's code.

## 1. Source-of-Truth Contracts Reused
- **Database Models & Session**: `backend/app/database.py` (`get_db`) and `backend/app/models.py` (`User`, `FamilyProfile`, `ApprovedPortal`, `Task`, `ConversationMessage`, `ToolAuditLog`).
- **Task States & Transitions**: `TaskState`, `TERMINAL_TASK_STATES`, `ALLOWED_STATE_TRANSITIONS`, and `_compute_pending_action_hash` in `backend/app/api/db_routes.py`.
- **Authentication & Ownership**: `resolve_authenticated_user_id` (`backend/app/services/auth_context.py`) delegates exclusively to `_verify_hs256_jwt`, `_allow_dev_user_header`, and `verify_caller_ownership` from `db_routes.py`. Client-supplied `user_id` values are never trusted on their own.
- **Portal Verification Tier**: `PortalSupportTier` in `backend/app/schemas/session.py` (`verified_official`, `known_portal`, `blocked`) matches `GET /api/v1/portals/verify` in `db_routes.py`.
- **Legacy / Incompatible DB Statuses**: `TaskState(task.status)` is parsed strictly. Any legacy non-canonical value (such as `"in_progress"`, which remains the ORM column default in `models.py`) raises `409 Conflict` clearly rather than being silently translated.

## 2. Process-Local Session Store (`SessionStore`)
- Because `backend/app/models.py` has no `Session` table and no Redis dependency is configured, `backend/app/services/session_service.py` implements an `InMemorySessionStore` behind a `SessionStore` interface.
- **Limitation**: Sessions live in process-local memory only; they are **lost on server restart** and **not shared across multiple Uvicorn/Gunicorn workers**.
- **Privacy**: Only sanitized canonical domain context (`SessionPortalContext`), `profile_scope`, `preferred_language`, and a boolean `vault_unlocked` flag are stored—never raw documents, passphrases, vault keys, IDs, OTPs, cookies, or page HTML.

## 3. Agent Gateway & Unconfigured Flows
- `backend/app/documents/agent_tools.py` exposes only Madhav's document/OCR tool callables (`DOCUMENT_AGENT_TOOLS`), not an agent planner or orchestration loop.
- `backend/app/services/agent_gateway.py` defines the `AgentGateway` interface with `UnconfiguredAgentGateway` as the active default:
  - **`POST /api/v1/goals`** (`goal_service.py`): Validates auth, user, profile, optional session, and portal context, then delegates to `AgentGateway.submit_goal()`. Returns `503 Service Unavailable` (`agent_orchestrator_unavailable`) until Ayush's orchestrator is connected; does not simulate agent success or invent tasks.
  - **`POST /api/v1/tasks/{task_id}/clarifications`** (`clarification_service.py`): Validates task ownership and checks `TaskState` is `awaiting_user_input` or `paused_for_auth_or_captcha`, sanitizes input, and delegates to `AgentGateway.submit_clarification()`. Returns `503 Service Unavailable` until Ayush's orchestrator is connected.
  - **`POST /api/v1/tasks/{task_id}/actions`** (`task_service.py`):
    - `cancel`, `approve_submit`, and `reject_submit` execute via `ALLOWED_STATE_TRANSITIONS` and FR-027 `action_hash` verification (`_compute_pending_action_hash`).
    - `pause` and `resume` have no generic `PAUSED` state in `TaskState` and no active orchestrator; they return `executed=False, manual_action_required=True` without mutating `task.status` or falsely claiming the task was paused/resumed.
