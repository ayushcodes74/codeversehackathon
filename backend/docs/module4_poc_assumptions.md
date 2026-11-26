# Anubhav Backend MVP Assumptions, Boundaries & Integration Blockers

This document records the current MVP assumptions, security boundaries, and remaining cross-module blockers for Anubhav-owned backend files (`backend/app/main.py`, `backend/app/core/`, `backend/app/schemas/`, `backend/app/llm/`, `backend/app/services/`, `backend/app/api/{health,sessions,goals,tasks,clarifications,conversations,router}.py`).

## 1. Contracts & Authentication Boundaries
- **Task State Contract (`backend/app/core/task_state.py`)**: After Ayush's `db_routes.py` refactor removed `TaskState`, `TERMINAL_TASK_STATES`, and `ALLOWED_STATE_TRANSITIONS`, Anubhav's schemas and services import them from `app.core.task_state`. Non-canonical DB statuses (such as `"paused"` or `"completed"` emitted by Ayush's `agent_routes.py`/`orchestrator.py`) are rejected explicitly with `409 Conflict` rather than silently translated.
- **Standalone Auth Context (`backend/app/services/auth_context.py`)**: Bearer HS256 JWTs are verified locally using `AppSettings.jwt_secret_key` (fail-closed `503` if unset or `< 32` UTF-8 bytes). Client-supplied `user_id` values are never trusted alone, and `X-User-Id` is disabled unless `app_env in ("development", "test")` and `ALLOW_DEV_USER_HEADER=true`.
- **Action-Hash Approval Gate (`backend/app/services/task_service.py`)**: `APPROVE_SUBMIT` and `REJECT_SUBMIT` require a matching `action_hash` (`_compute_pending_action_hash`). Approval transitions the task to `submitting` (not `submitted`) because the client must still perform the portal submit and return verifiable outcome evidence.

## 2. Process-Local Session Store (`backend/app/services/session_service.py`)
- Because `backend/app/models.py` defines no `Session` table and no Redis store is configured, `InMemorySessionStore` implements `SessionStore` in process-local memory.
- **Limits**: Sessions are **lost on server restart** and **not shared across multiple workers**.
- **Privacy**: Only sanitized domain context (`SessionPortalContext`), `profile_scope`, `preferred_language`, and a boolean `vault_unlocked` flag are stored—never raw documents, passphrases, vault keys, IDs, OTPs, cookies, or raw page content.

## 3. Orchestrator, Clarification, Pause/Resume & LLM Status
- **Unauthenticated Agent Router Left Unmounted**: `backend/app/api/agent_routes.py` (Ayush's router) has no caller authentication or user/task ownership checks, so `backend/app/api/router.py` intentionally does **not** mount it.
- **In-Process `OrchestratorAgentGateway` (`backend/app/services/agent_gateway.py`)**: Enforces caller, task, profile, session, and domain-shield ownership before calling Ayush's `execute_agent_turn` in-process.
  - **Clarifications**: Only general-fact clarifications in `_MEMORY_CLARIFICATION_KEYS` (`domicile_state`, `state`, `education_level`, `education`) can be resumed via the orchestrator; other clarification types fail safely with `409 Conflict` (`manual_action_required` / complete manually).
  - **Pause / Resume (`POST /api/v1/tasks/{task_id}/actions`)**: Returns `executed=False, manual_action_required=True` without mutating `task.status`, because `TaskState` has no generic `PAUSED` state and `execute_agent_turn` requires a fresh client DOM snapshot to resume.
- **Fail-Closed LLM Client (`backend/app/llm/`)**: No LLM provider or model is selected in the repository. `get_llm_client()` returns `UnconfiguredLLMClient` and raises `503 llm_not_configured` until the team approves a provider, model, and SDK.

## 4. Remaining Owner Requests / Blockers
1. **Ayush (`backend/app/api/agent_routes.py`, `backend/app/api/db_routes.py`)**: Add caller authentication/ownership checks to `db_routes.py` and `agent_routes.py` if those routes are to be exposed directly, and align `orchestrator.py` / `models.py` statuses (`"paused"`, `"completed"`) with canonical `TaskState` (`awaiting_submit_approval`, `submitted`).
2. **Ayush (`backend/app/models.py`)**: Add a persistent `Session` model/migration if sessions must survive restarts or multi-worker deployments.
3. **Team**: Select the approved LLM provider/model and SDK before implementing an adapter under `backend/app/llm/`.
4. **Madhav (`backend/app/api/documents.py`)**: Confirm whether `/documents/*` remains at root or moves under `/api/v1/documents/*`.
