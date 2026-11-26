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
- **Unauthenticated Agent Router Left Unmounted**: `backend/app/api/agent_routes.py` (Ayush's router) leaves `/guides/*` unauthenticated, omits `profile_id` ownership validation on `/chat`, and mutates `task.status` directly on `/pause` and `/resume`, so `backend/app/api/router.py` intentionally does **not** mount it.
- **Protected Extension Chat Endpoint (`backend/app/api/agent.py`)**: Exposes `POST /api/v1/agent/chat` exclusively through Anubhav's caller/user/profile/task ownership enforcement (`resolve_authenticated_user_id` + `OrchestratorAgentGateway.run_chat_turn`) so the Chrome extension works safely without mounting `agent_routes.py`.
- **In-Process `OrchestratorAgentGateway` (`backend/app/services/agent_gateway.py`)**: Enforces caller, task, profile, session, and domain-shield ownership before calling Ayush's `execute_agent_turn` in-process.
  - **Clarifications**: Only general-fact clarifications in `_MEMORY_CLARIFICATION_KEYS` (`domicile_state`, `state`, `education_level`, `education`) can be resumed via the orchestrator; other clarification types fail safely with `409 Conflict` (`manual_action_required` / complete manually).
  - **Pause / Resume (`POST /api/v1/tasks/{task_id}/actions`)**: Returns `executed=False, manual_action_required=True` without mutating `task.status`, because `TaskState` has no generic `PAUSED` state and `execute_agent_turn` requires a fresh client DOM snapshot and active orchestration loop.
- **Fail-Closed LLM Client (`backend/app/llm/`)**: No LLM provider or model is selected in the repository. `get_llm_client()` returns `UnconfiguredLLMClient` and raises `503 llm_not_configured` until the team approves a provider, model, and SDK.

## 4. Remaining Owner Requests / Blockers
1. **Ayush (`backend/app/api/agent_routes.py`, `backend/app/agent/orchestrator.py`)**: Add auth to `/guides/*`, add `profile_id` ownership verification in `agent_routes.py`/`orchestrator.py`, and align `agent_routes.py` `/pause` & `/resume` with `ALLOWED_STATE_TRANSITIONS` if that router is ever to be mounted directly.
2. **Ayush & Team (`JWT_SECRET_KEY`)**: `db_routes.py` `/api/v1/auth/dev-token` uses a local fallback secret if `JWT_SECRET_KEY` is unset, while Anubhav's `auth_context.py` fails closed (`503`) unless `JWT_SECRET_KEY` (`>= 32` bytes) is explicitly set in `backend/.env`. Ensure `JWT_SECRET_KEY` is set in `backend/.env` before running the extension dev-login flow.
3. **Ayush (`backend/app/models.py`)**: Add a persistent `Session` model/migration if sessions must survive restarts or multi-worker deployments.
4. **Shubh (`chrome-extension/sidepanel.js`)**: `sidepanel.js` checks `verdict.support_tier === 'local_dev_allowed'` on `GET /api/v1/portals/verify`, whereas `db_routes.py` returns `'verified_official'`, `'known_portal'`, or `'blocked'`; align or handle local demo host checks accordingly, and use `GET /api/v1/tasks` / `POST /api/v1/tasks/{task_id}/actions` if adding task list/cancel UI.
5. **Team**: Select the approved LLM provider/model and SDK before implementing an adapter under `backend/app/llm/`.
6. **Madhav (`backend/app/api/documents.py`)**: Confirm whether `/documents/*` remains at root or moves under `/api/v1/documents/*`.
