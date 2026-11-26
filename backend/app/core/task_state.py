"""Anubhav-owned canonical task state enum and transition table.

Created so Module 2 schemas and Module 4 services do not break after Ayush's
db_routes.py refactor removed TaskState, TERMINAL_TASK_STATES, and
ALLOWED_STATE_TRANSITIONS from app.api.db_routes.
"""

from enum import Enum


class TaskState(str, Enum):
    """Allowed workflow states for task lifecycle and Safe-Pause checkpoints."""

    IN_PROGRESS = "in_progress"
    DISCOVERING = "discovering"
    PLANNING = "planning"
    COLLECTING_DOCS = "collecting_docs"
    NAVIGATING = "navigating"
    INSPECTING_FORM = "inspecting_form"
    MAPPING_FIELDS = "mapping_fields"
    FILLING = "filling"
    AWAITING_USER_INPUT = "awaiting_user_input"
    PAUSED_FOR_AUTH_OR_CAPTCHA = "paused_for_auth_or_captcha"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_SUBMIT_APPROVAL = "awaiting_submit_approval"
    SUBMITTING = "submitting"
    VERIFYING = "verifying"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATES = frozenset(
    {TaskState.SUBMITTED, TaskState.FAILED, TaskState.CANCELLED}
)

ALLOWED_STATE_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.IN_PROGRESS: frozenset(
        {
            TaskState.DISCOVERING,
            TaskState.PLANNING,
            TaskState.COLLECTING_DOCS,
            TaskState.NAVIGATING,
            TaskState.INSPECTING_FORM,
            TaskState.MAPPING_FIELDS,
            TaskState.FILLING,
            TaskState.AWAITING_USER_INPUT,
            TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.DISCOVERING: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.PLANNING,
            TaskState.COLLECTING_DOCS,
            TaskState.NAVIGATING,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PLANNING: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.COLLECTING_DOCS,
            TaskState.NAVIGATING,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.COLLECTING_DOCS: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.FILLING,
            TaskState.MAPPING_FIELDS,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.NAVIGATING: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.INSPECTING_FORM,
            TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.INSPECTING_FORM: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.MAPPING_FIELDS,
            TaskState.COLLECTING_DOCS,
            TaskState.FILLING,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.MAPPING_FIELDS: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.FILLING,
            TaskState.AWAITING_USER_INPUT,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.FILLING: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA,
            TaskState.AWAITING_USER_INPUT,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.AWAITING_USER_INPUT: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.DISCOVERING,
            TaskState.PLANNING,
            TaskState.COLLECTING_DOCS,
            TaskState.NAVIGATING,
            TaskState.INSPECTING_FORM,
            TaskState.MAPPING_FIELDS,
            TaskState.FILLING,
            TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PAUSED_FOR_AUTH_OR_CAPTCHA: frozenset(
        {
            TaskState.IN_PROGRESS,
            TaskState.NAVIGATING,
            TaskState.INSPECTING_FORM,
            TaskState.MAPPING_FIELDS,
            TaskState.FILLING,
            TaskState.AWAITING_APPROVAL,
            TaskState.AWAITING_SUBMIT_APPROVAL,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.AWAITING_APPROVAL: frozenset(
        {
            TaskState.SUBMITTING,
            TaskState.FILLING,
            TaskState.CANCELLED,
            TaskState.FAILED,
        }
    ),
    TaskState.AWAITING_SUBMIT_APPROVAL: frozenset(
        {
            TaskState.SUBMITTING,
            TaskState.FILLING,
            TaskState.CANCELLED,
            TaskState.FAILED,
        }
    ),
    TaskState.SUBMITTING: frozenset(
        {
            TaskState.VERIFYING,
            TaskState.SUBMITTED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.VERIFYING: frozenset(
        {
            TaskState.SUBMITTED,
            TaskState.FAILED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.SUBMITTED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}
