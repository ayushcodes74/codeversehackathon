"""AI Agent Orchestrator for Universal AI Web Assistant.

Implements the Observe -> Plan -> Select Tool -> Execute -> Verify state machine:
- Two Intent Lanes:
  1. Mid-Form Knowledge / Visual Guide Q&A (non-destructive, keeps active form state)
  2. Form Automation Actions (Observe DOM, Domain Shield, Fill Fields, Gate on Submit, Capture Receipt)
- Safe-Pause & Resume Later (persisting checkpoint_state into tasks table using canonical TaskState)
- Zero-Leak Hybrid Storage (emitting vault references instead of plain digits)
- Action-Bound Approval Gate (PRD FR-027) & Verified Receipt Gate (PRD FR-061).
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.discovery import check_domain_shield, discover_or_narrow_portal
from app.agent.tool_registry import (
    audit_log_tool_call,
    capture_receipt_tool,
    fill_form_fields_tool,
    inspect_page_tool,
    map_form_fields_tool,
    request_approval_tool,
)
from app.agent.visual_guides import get_visual_guide_card, is_visual_guide_query
from app.api.db_routes import (
    TERMINAL_TASK_STATES,
    TaskState,
    _compute_pending_action_hash,
)
from app.models import AgentMemory, FamilyProfile, Task, ToolAuditLog
from app.policies.log_redactor import redact_dict
from app.schemas.agent_contracts import (
    AgentChatRequest,
    AgentStepResponse,
    CaptureReceiptInput,
    ClientAction,
    FieldMapping,
    FillFormFieldsInput,
    InspectPageInput,
    MapFormFieldsInput,
    RequestApprovalInput,
)

_NON_FILLABLE_INPUT_TYPES = frozenset(
    {"hidden", "submit", "button", "reset", "file", "checkbox", "radio", "image"}
)


def _normalize_field_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def _parse_reply_into_collected_data(
    reply_text: str,
    pending_fields: list[str],
    fallback_key: str,
) -> dict[str, str]:
    """Parse user clarification reply (single, key:value pairs, or comma-separated) into normalized keys."""
    parsed: dict[str, str] = {}
    cleaned = reply_text.strip()
    if not cleaned:
        return parsed

    parsed[fallback_key] = cleaned

    # Try explicit key: value or key = value segments (separated by newline, comma, or semicolon)
    segments = [seg.strip() for seg in re.split(r"[\n;]+|,(?=\s*[A-Za-z][^,:]{0,40}[:=])", cleaned) if seg.strip()]
    matched_kv = False
    for seg in segments:
        kv = re.match(r"^([A-Za-z0-9 _/-]{1,50})\s*[:=]\s*(.+)$", seg)
        if kv:
            raw_k, raw_v = kv.group(1).strip(), kv.group(2).strip()
            norm_k = _normalize_field_key(raw_k)
            if norm_k and raw_v:
                parsed[norm_k] = raw_v
                matched_kv = True

    if matched_kv:
        # Also map onto matching pending_fields normalized keys if suffix/prefix matches
        for lbl in pending_fields:
            lbl_key = _normalize_field_key(lbl)
            if lbl_key not in parsed:
                for k, v in list(parsed.items()):
                    if k and (k in lbl_key or lbl_key in k):
                        parsed[lbl_key] = v
                        break
        return parsed

    # If multiple pending fields were asked and user replied comma-separated values cleanly
    if len(pending_fields) > 1:
        parts = [p.strip() for p in re.split(r"[,;\n]+", cleaned) if p.strip()]
        if len(parts) == len(pending_fields):
            for lbl, val in zip(pending_fields, parts):
                parsed[_normalize_field_key(lbl)] = val
            return parsed

    if len(pending_fields) == 1:
        parsed[_normalize_field_key(pending_fields[0])] = cleaned

    return parsed

_ACTIVE_TASK_STATUSES = tuple(
    s.value for s in TaskState if s not in TERMINAL_TASK_STATES
)


async def get_or_create_task(
    db: AsyncSession,
    user_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    goal_text: str,
    target_url: str | None = None,
) -> Task:
    """Find an existing non-terminal task owned by user_id or create a new one."""
    if task_id:
        stmt = select(Task).where(Task.id == task_id)
        res = await db.execute(stmt)
        task = res.scalar_one_or_none()
        if task:
            if str(task.user_id) != str(user_id):
                raise PermissionError("Forbidden: caller does not own requested task_id.")
            if target_url and not task.target_url:
                task.target_url = target_url
            return task

    stmt = (
        select(Task)
        .where(Task.user_id == user_id, Task.status.in_(_ACTIVE_TASK_STATUSES))
        .order_by(desc(Task.updated_at))
    )
    if profile_id:
        stmt = stmt.where(Task.profile_id == profile_id)
    res = await db.execute(stmt)
    existing_task = res.scalars().first()
    if existing_task:
        if target_url and not existing_task.target_url:
            existing_task.target_url = target_url
        return existing_task

    resolved_profile_id = profile_id
    if not resolved_profile_id:
        prof_res = await db.execute(
            select(FamilyProfile).where(
                FamilyProfile.user_id == user_id,
                FamilyProfile.is_default == True,  # noqa: E712
            )
        )
        def_prof = prof_res.scalar_one_or_none()
        if def_prof:
            resolved_profile_id = def_prof.id
        else:
            any_prof_res = await db.execute(
                select(FamilyProfile).where(FamilyProfile.user_id == user_id)
            )
            any_prof = any_prof_res.scalars().first()
            if any_prof:
                resolved_profile_id = any_prof.id
            else:
                raise ValueError("No family profile found for user. Please authenticate first.")

    new_task = Task(
        user_id=user_id,
        profile_id=resolved_profile_id,
        goal_text=goal_text or "General Web Automation",
        target_url=target_url,
        status=TaskState.DISCOVERING.value,
        current_step_index=1,
        total_steps=4,
        checkpoint_state={"step": 1, "history": []},
    )
    db.add(new_task)
    await db.flush()
    return new_task


async def fetch_profile_memories(
    db: AsyncSession,
    user_id: uuid.UUID,
    profile_id: uuid.UUID,
) -> tuple[str, dict[str, str]]:
    """Retrieve profile slug and all stored memory facts for the profile."""
    p_stmt = select(FamilyProfile).where(FamilyProfile.id == profile_id)
    p_res = await db.execute(p_stmt)
    prof = p_res.scalar_one_or_none()
    slug = prof.profile_slug if prof else "self"

    m_stmt = select(AgentMemory).where(
        AgentMemory.user_id == user_id,
        AgentMemory.profile_id == profile_id,
    )
    m_res = await db.execute(m_stmt)
    memories: dict[str, str] = {}
    for m in m_res.scalars().all():
        memories[m.fact_key.lower()] = m.fact_value

    return slug, memories


async def _has_verified_submit_approval(db: AsyncSession, task_id: uuid.UUID) -> bool:
    """Verify via ToolAuditLog that POST /api/v1/tasks/{task_id}/approval approved submission."""
    stmt = (
        select(ToolAuditLog.id)
        .where(
            ToolAuditLog.task_id == task_id,
            ToolAuditLog.tool_name == "task_submit_approval",
            ToolAuditLog.status == "approved",
        )
        .limit(1)
    )
    res = await db.execute(stmt)
    return res.scalar_one_or_none() is not None


async def execute_agent_turn(
    request: AgentChatRequest,
    db: AsyncSession,
) -> AgentStepResponse:
    """Core state machine executing one turn of the agent."""
    user_msg = request.message or ""
    lang = request.language or "en"
    url = request.current_url or ""
    dom = request.dom_snapshot

    # =================================================================
    # LANE 1: Mid-Form Knowledge / Visual Guide Q&A (Non-Destructive)
    # =================================================================
    if is_visual_guide_query(user_msg):
        card = get_visual_guide_card(user_msg, language=lang)
        if card:
            task_id = request.task_id
            active_status = TaskState.DISCOVERING.value
            if not task_id:
                stmt = (
                    select(Task)
                    .where(
                        Task.user_id == request.user_id,
                        Task.status.in_(_ACTIVE_TASK_STATUSES),
                    )
                    .order_by(desc(Task.updated_at))
                )
                res = await db.execute(stmt)
                active_task = res.scalars().first()
                if active_task:
                    task_id = active_task.id
                    active_status = active_task.status

            msg_en = f"Here is visual guidance for finding your {card.title}."
            msg_hi = f"यहाँ आपके {card.title_hi} को खोजने के लिए दृश्य मार्गदर्शन है।"

            return AgentStepResponse(
                task_id=task_id,
                response_type="visual_guide",
                intent_lane="knowledge_qa",
                message=msg_hi if lang == "hi" else msg_en,
                message_hi=msg_hi,
                visual_guide=card,
                client_actions=[],
                checkpoint_saved=False,
                status=active_status,
            )

    # =================================================================
    # LANE 2: Form Automation & Workflow State Machine
    # =================================================================

    # 1. Official Domain Shield Check
    if url:
        shield_res = await check_domain_shield(url, db)
        if not shield_res.is_allowed:
            return AgentStepResponse(
                task_id=request.task_id,
                response_type="error",
                intent_lane="form_automation",
                message=shield_res.warning or "Access blocked by Official Domain Shield.",
                message_hi="आधिकारिक डोमेन शील्ड द्वारा अनधिकृत वेबसाइट अवरुद्ध की गई है।",
                status=TaskState.AWAITING_USER_INPUT.value,
            )

    # 2. Get or Create Task Checkpoint
    task = await get_or_create_task(
        db=db,
        user_id=request.user_id,
        profile_id=request.profile_id,
        task_id=request.task_id,
        goal_text=user_msg,
        target_url=url,
    )

    # Record user reply into collected_data / clarification_answers and advance AWAITING_USER_INPUT -> FILLING
    has_visible_form_fields = bool(dom and getattr(dom, "fields", None) and len(dom.fields) > 0)
    answered_clarification_key: str | None = None
    if task.status == TaskState.AWAITING_USER_INPUT.value and user_msg.strip():
        checkpoint = dict(task.checkpoint_state or {})
        pending_missing = list(checkpoint.pop("pending_missing_fields", None) or [])
        answered_key = str(checkpoint.pop("pending_clarification_key", None) or "user_reply")
        answered_clarification_key = answered_key
        answers = dict(checkpoint.get("clarification_answers") or {})
        collected = dict(checkpoint.get("collected_data") or {})
        reply_text = user_msg.strip()
        answers[answered_key] = reply_text
        parsed_entries = _parse_reply_into_collected_data(reply_text, pending_missing, answered_key)
        collected.update(parsed_entries)
        checkpoint["clarification_answers"] = answers
        checkpoint["collected_data"] = collected
        task.checkpoint_state = checkpoint
        task.status = TaskState.FILLING.value

        if answered_key in {"domicile_state", "state", "education_level", "education"}:
            mem_stmt = select(AgentMemory).where(
                AgentMemory.user_id == request.user_id,
                AgentMemory.profile_id == task.profile_id,
                AgentMemory.fact_key == answered_key,
            )
            existing_mem = (await db.execute(mem_stmt)).scalar_one_or_none()
            if existing_mem:
                existing_mem.fact_value = reply_text
                existing_mem.category = "clarification"
                existing_mem.source = "user_confirmed"
            else:
                db.add(
                    AgentMemory(
                        user_id=request.user_id,
                        profile_id=task.profile_id,
                        category="clarification",
                        fact_key=answered_key,
                        fact_value=reply_text,
                        source="user_confirmed",
                    )
                )
        await db.flush()

    # 3. Skip discovery questions completely if form DOM snapshot already has visible fields.
    if not dom or (not has_visible_form_fields and not getattr(dom, "buttons", None) and not (getattr(dom, "text_content_snippet", "") or "").strip()):
        portal_item, clarify_q, disc_msg = await discover_or_narrow_portal(
            user_id=request.user_id,
            profile_id=task.profile_id,
            goal_text=task.goal_text or user_msg,
            language=lang,
            db=db,
        )

        # Do not loop back into clarification if the task just received a user reply or already answered this key.
        same_or_answered = (
            clarify_q is not None
            and (
                answered_clarification_key == clarify_q.missing_field_key
                or clarify_q.missing_field_key in ((task.checkpoint_state or {}).get("clarification_answers") or {})
                or answered_clarification_key is not None
            )
        )
        if clarify_q and not same_or_answered:
            checkpoint = dict(task.checkpoint_state or {})
            checkpoint["pending_clarification_key"] = clarify_q.missing_field_key
            task.checkpoint_state = checkpoint
            task.status = TaskState.AWAITING_USER_INPUT.value
            await db.flush()
            return AgentStepResponse(
                task_id=task.id,
                response_type="clarification",
                intent_lane="form_automation",
                message=clarify_q.question_hi if lang == "hi" else clarify_q.question_en,
                message_hi=clarify_q.question_hi,
                next_tool="request_user_input",
                tool_input=clarify_q.model_dump(),
                current_step_index=task.current_step_index,
                total_steps=task.total_steps,
                status=task.status,
            )

        if portal_item and answered_clarification_key is None:
            task.portal_id = portal_item.id
            task.target_url = portal_item.entry_url
            task.status = TaskState.NAVIGATING.value
            await db.flush()

            nav_action = ClientAction(
                action_type="NAVIGATE",
                value_or_vault_ref=portal_item.entry_url,
                description=f"Navigating to {portal_item.portal_name}",
            )
            return AgentStepResponse(
                task_id=task.id,
                response_type="action",
                intent_lane="form_automation",
                message=disc_msg,
                message_hi=disc_msg if lang == "hi" else None,
                next_tool="navigate_to_portal",
                client_actions=[nav_action],
                current_step_index=1,
                total_steps=4,
                status=task.status,
            )

        if answered_clarification_key is not None:
            task.status = TaskState.FILLING.value
            await db.flush()
            return AgentStepResponse(
                task_id=task.id,
                response_type="action",
                intent_lane="form_automation",
                message="Clarification saved. Advancing to form filling — open or inspect the form page.",
                message_hi="जानकारी सहेजी गई। फॉर्म भरने के लिए तैयार — कृपया फॉर्म पेज खोलें।",
                current_step_index=task.current_step_index,
                total_steps=task.total_steps,
                status=task.status,
            )

        task.status = TaskState.PLANNING.value
        await db.flush()
        return AgentStepResponse(
            task_id=task.id,
            response_type="action",
            intent_lane="form_automation",
            message="Ready. Please open the portal or provide the page URL.",
            message_hi="तैयार। कृपया पोर्टल खोलें या पेज URL प्रदान करें।",
            status=task.status,
        )

    # 4. Observe: Inspect DOM Snapshot
    inspect_in = InspectPageInput(dom_snapshot=dom, current_url=url)
    inspect_out = await inspect_page_tool(inspect_in)
    await audit_log_tool_call(
        db,
        task.id,
        "inspect_page",
        "success",
        inspect_in.model_dump(),
        inspect_out.model_dump(),
    )

    task.current_step_index = inspect_out.current_step_number
    task.total_steps = inspect_out.total_steps

    # Case A: Post-Submission Receipt / Acknowledgement Screen (PRD FR-061 & FR-027)
    if inspect_out.page_type == "acknowledgement_receipt":
        if not await _has_verified_submit_approval(db, task.id):
            task.status = TaskState.AWAITING_SUBMIT_APPROVAL.value
            task.checkpoint_state = {
                **(task.checkpoint_state or {}),
                "awaiting_approval": True,
                "blocked_reason": "missing_verified_submit_approval",
            }
            await db.flush()

            pending_action_hash = _compute_pending_action_hash(task)
            approval_out = await request_approval_tool(
                RequestApprovalInput(
                    action_type="submit_form",
                    summary_of_changes={"reason": "DOM receipt detected without verified human submit approval"},
                    button_selector="#btn-final-submit",
                    button_text="Submit Application",
                    warning_message_en=(
                        "Submission blocked: No verified human approval record exists in audit logs. "
                        "Approve via /approval before finalizing."
                    ),
                    warning_message_hi=(
                        "सबमिशन अवरुद्ध: कोई सत्यापित मानवीय स्वीकृति रिकॉर्ड नहीं मिला। "
                        "अंतिम पुष्टि से पहले /approval के माध्यम से स्वीकृति दें।"
                    ),
                )
            )
            approval_out.action_payload["action_hash"] = pending_action_hash
            approval_out.action_payload["approval_endpoint"] = f"/api/v1/tasks/{task.id}/approval"

            await audit_log_tool_call(
                db,
                task.id,
                "capture_receipt_blocked",
                "rejected",
                {"url": url, "page_type": inspect_out.page_type},
                {"reason": "No verified task_submit_approval record found", "status": task.status},
            )

            msg_en = "Submission transition rejected: verified human approval via /approval is required before completing submission."
            msg_hi = "जमा स्थिति अस्वीकार की गई: पूर्ण करने से पहले सत्यापित मानवीय स्वीकृति आवश्यक है।"
            return AgentStepResponse(
                task_id=task.id,
                response_type="approval_request",
                intent_lane="form_automation",
                message=msg_hi if lang == "hi" else msg_en,
                message_hi=msg_hi,
                next_tool="request_approval",
                approval_request=approval_out,
                client_actions=[],
                checkpoint_saved=True,
                current_step_index=task.current_step_index,
                total_steps=task.total_steps,
                status=task.status,
            )

        receipt_in = CaptureReceiptInput(dom_snapshot=dom)
        receipt_out = await capture_receipt_tool(receipt_in)
        await audit_log_tool_call(
            db,
            task.id,
            "capture_receipt",
            "success" if receipt_out.is_success else "unconfirmed",
            receipt_in.model_dump(),
            receipt_out.model_dump(),
        )

        if not receipt_out.is_success or not receipt_out.receipt_number:
            task.status = TaskState.VERIFYING.value
            task.checkpoint_state = {
                "completed": False,
                "receipt_verified": False,
                "receipt": receipt_out.model_dump(),
            }
            await db.flush()

            msg_en = (
                "Submission receipt could not be verified on this page. "
                "Please confirm your acknowledgement reference number before marking submitted."
            )
            msg_hi = (
                "इस पृष्ठ पर रसीद संख्या सत्यापित नहीं हो सकी। "
                "कृपया जमा स्थिति की पुष्टि करने से पहले अपनी संदर्भ संख्या जांचें।"
            )
            return AgentStepResponse(
                task_id=task.id,
                response_type="clarification",
                intent_lane="form_automation",
                message=msg_hi if lang == "hi" else msg_en,
                message_hi=msg_hi,
                receipt_data=receipt_out,
                checkpoint_saved=True,
                current_step_index=task.current_step_index,
                total_steps=task.total_steps,
                status=task.status,
            )

        task.application_ref_no = receipt_out.receipt_number
        task.status = TaskState.SUBMITTED.value
        task.checkpoint_state = {
            "completed": True,
            "application_ref_no": receipt_out.receipt_number,
            "receipt": receipt_out.model_dump(),
        }
        await db.flush()

        msg_en = f"Application successfully submitted! Reference No: {receipt_out.receipt_number}."
        msg_hi = f"आवेदन सफलतापूर्वक जमा हो गया! संदर्भ संख्या (Ref No): {receipt_out.receipt_number}।"

        return AgentStepResponse(
            task_id=task.id,
            response_type="completed",
            intent_lane="form_automation",
            message=msg_hi if lang == "hi" else msg_en,
            message_hi=msg_hi,
            receipt_data=receipt_out,
            checkpoint_saved=True,
            current_step_index=task.total_steps,
            total_steps=task.total_steps,
            status=task.status,
        )

    # Case B: Action-Bound Human-in-the-Loop Gate on Review & Final Submit Step (PRD FR-027)
    # Never treat a page with unfilled editable inputs as final review; Case C must fill them first.
    has_empty_fillable_fields = any(
        (f.tag_name or "").lower() in ("input", "select", "textarea")
        and not f.is_disabled
        and (f.input_type or "text").lower() not in _NON_FILLABLE_INPUT_TYPES
        and not (f.current_value and str(f.current_value).strip())
        for f in dom.fields
    )
    is_review_step = (
        inspect_out.page_type == "review_page"
        or "step 4" in (dom.step_indicator or "").lower()
        or any("submit" in b.text.lower() for b in dom.buttons)
    ) and not has_empty_fillable_fields

    has_verified_approval = await _has_verified_submit_approval(db, task.id)
    if is_review_step and not has_verified_approval:
        submit_btn = next((b for b in dom.buttons if "submit" in b.text.lower()), None)
        btn_sel = submit_btn.selector if submit_btn else "#btn-final-submit"
        btn_txt = submit_btn.text if submit_btn else "Submit Application"

        collected_map = {
            _normalize_field_key(k): str(v)
            for k, v in ((task.checkpoint_state or {}).get("collected_data") or {}).items()
            if v is not None and str(v).strip()
        }
        raw_summary: dict[str, str] = {}
        for f in dom.fields[:20]:
            lbl = f.label or f.element_key
            val = f.current_value if (f.current_value and str(f.current_value).strip()) else collected_map.get(_normalize_field_key(lbl), "[Pending]")
            raw_summary[lbl] = val
        summary_changes = redact_dict(raw_summary)
        req_approval_in = RequestApprovalInput(
            action_type="submit_form",
            summary_of_changes=summary_changes,
            button_selector=btn_sel,
            button_text=btn_txt,
            warning_message_en="Human Approval Required: Please verify all filled details before final submission. Antigravity will never auto-submit government applications.",
            warning_message_hi="मानवीय स्वीकृति आवश्यक: कृपया अंतिम जमा करने से पहले सभी भरे हुए विवरणों की पुष्टि करें। हम कभी भी स्वचालित रूप से अंतिम आवेदन जमा नहीं करते।",
        )
        approval_out = await request_approval_tool(req_approval_in)

        task.status = TaskState.AWAITING_SUBMIT_APPROVAL.value
        task.checkpoint_state = {
            "awaiting_approval": True,
            "step": inspect_out.current_step_number,
            "summary": summary_changes,
        }
        await db.flush()

        pending_action_hash = _compute_pending_action_hash(task)
        approval_out.action_payload["action_hash"] = pending_action_hash
        approval_out.action_payload["approval_endpoint"] = f"/api/v1/tasks/{task.id}/approval"

        await audit_log_tool_call(
            db,
            task.id,
            "request_approval",
            "success",
            req_approval_in.model_dump(),
            approval_out.model_dump(),
        )

        msg_en = "Step 4 Review complete. Action-bound approval via /approval required before clicking final Submit."
        msg_hi = "चरण 4 समीक्षा पूर्ण। अंतिम सबमिट करने से पहले कार्रवाई-बद्ध मानवीय स्वीकृति आवश्यक है।"

        return AgentStepResponse(
            task_id=task.id,
            response_type="approval_request",
            intent_lane="form_automation",
            message=msg_hi if lang == "hi" else msg_en,
            message_hi=msg_hi,
            next_tool="request_approval",
            approval_request=approval_out,
            client_actions=[],
            checkpoint_saved=True,
            current_step_index=inspect_out.current_step_number,
            total_steps=inspect_out.total_steps,
            status=task.status,
        )

    if is_review_step and has_verified_approval:
        submit_btn = next((b for b in dom.buttons if "submit" in b.text.lower()), None)
        btn_sel = submit_btn.selector if submit_btn else "#btn-final-submit"
        btn_txt = submit_btn.text if submit_btn else "Submit Application"
        btn_key = submit_btn.element_key if submit_btn else "btn_submit"

        submit_action = ClientAction(
            action_type="CLICK_BUTTON",
            element_key=btn_key,
            selector=btn_sel,
            field_type="submit",
            is_sensitive=False,
            description=f"Click verified submit button '{btn_txt}'",
        )
        task.status = TaskState.SUBMITTING.value
        task.checkpoint_state = {
            **(task.checkpoint_state or {}),
            "awaiting_approval": False,
            "submit_dispatched": True,
            "step": inspect_out.current_step_number,
        }
        await db.flush()

        await audit_log_tool_call(
            db,
            task.id,
            "submit_form",
            "dispatched",
            {"selector": btn_sel, "button_text": btn_txt},
            {"action_type": "CLICK_BUTTON", "status": task.status},
        )

        msg_en = f"Verified human approval received. Clicking '{btn_txt}' to submit your application."
        msg_hi = f"सत्यापित मानवीय स्वीकृति प्राप्त हुई। आपका आवेदन जमा करने के लिए '{btn_txt}' पर क्लिक किया जा रहा है।"
        return AgentStepResponse(
            task_id=task.id,
            response_type="action",
            intent_lane="form_automation",
            message=msg_hi if lang == "hi" else msg_en,
            message_hi=msg_hi,
            next_tool="submit_form",
            client_actions=[submit_action],
            checkpoint_saved=True,
            current_step_index=inspect_out.current_step_number,
            total_steps=inspect_out.total_steps,
            status=task.status,
        )

    # Case C: Form Step Automation (Map & Fill Fields)
    profile_slug, memories = await fetch_profile_memories(db, task.user_id, task.profile_id)
    checkpoint_data = dict(task.checkpoint_state or {})
    collected_data = {
        _normalize_field_key(k): str(v)
        for k, v in (checkpoint_data.get("collected_data") or {}).items()
        if v is not None and str(v).strip()
    }
    combined_memories = dict(memories)
    for c_key, c_val in collected_data.items():
        combined_memories[c_key] = c_val
        if c_key in ("pin_code", "postal_code"):
            combined_memories["pincode"] = c_val
        elif c_key in ("annual_family_income", "family_income"):
            combined_memories["annual_income"] = c_val
        elif c_key == "domicile_state":
            combined_memories["state"] = c_val

    map_in = MapFormFieldsInput(
        fields=dom.fields,
        profile_slug=profile_slug,
        available_memories=combined_memories,
    )
    map_out = await map_form_fields_tool(map_in)

    # Supplement map_out with direct normalized label matches from collected_data
    mapped_element_keys = {m.element_key for m in map_out.mappings}
    extra_mappings: list[FieldMapping] = []
    for f in dom.fields:
        if f.element_key in mapped_element_keys or f.is_disabled:
            continue
        if (f.input_type or "text").lower() in _NON_FILLABLE_INPUT_TYPES:
            continue
        raw_label = f.label or f.name or f.placeholder or f.id or ""
        norm_label = _normalize_field_key(raw_label)
        matched_val = collected_data.get(norm_label)
        if matched_val is None and norm_label:
            for c_key, c_val in collected_data.items():
                if c_key and c_key != "user_reply" and (c_key == norm_label or c_key in norm_label or norm_label in c_key):
                    matched_val = c_val
                    break
        if matched_val:
            extra_mappings.append(
                FieldMapping(
                    element_key=f.element_key,
                    field_label=raw_label or f.element_key,
                    selector=f.selector,
                    matched_fact_key=norm_label,
                    value_or_vault_ref=matched_val,
                    is_sensitive=False,
                    confidence=0.95,
                    field_type=f.input_type or "text",
                )
            )
            mapped_element_keys.add(f.element_key)

    all_mappings = list(map_out.mappings) + extra_mappings

    # Check whether any visible/required fillable form field still has no value or vault mapping
    missing_labels: list[str] = []
    for f in dom.fields:
        if f.element_key in mapped_element_keys or f.is_disabled:
            continue
        if (f.input_type or "text").lower() in _NON_FILLABLE_INPUT_TYPES:
            continue
        if f.current_value and str(f.current_value).strip():
            continue
        label_text = (f.label or f.name or f.placeholder or f.id or f.element_key).strip()
        if label_text and label_text not in missing_labels:
            missing_labels.append(label_text)

    if missing_labels:
        if len(missing_labels) > 2:
            missing_joined = f"{', '.join(missing_labels[:-1])}, and {missing_labels[-1]}"
        elif len(missing_labels) == 2:
            missing_joined = f"{missing_labels[0]} and {missing_labels[1]}"
        else:
            missing_joined = missing_labels[0]
        msg_en = f"Please provide your {missing_joined} to complete {inspect_out.active_step}."
        msg_hi = f"{inspect_out.active_step} पूरा करने के लिए कृपया अपना {missing_joined} बताएं।"
        task.status = TaskState.AWAITING_USER_INPUT.value
        task.checkpoint_state = {
            **checkpoint_data,
            "step": inspect_out.current_step_number,
            "step_name": inspect_out.active_step,
            "url": url,
            "pending_missing_fields": missing_labels,
            "pending_clarification_key": _normalize_field_key(missing_labels[0]),
        }
        await db.flush()
        return AgentStepResponse(
            task_id=task.id,
            response_type="clarification",
            intent_lane="form_automation",
            message=msg_hi if lang == "hi" else msg_en,
            message_hi=msg_hi,
            next_tool="request_user_input",
            tool_input={
                "missing_field_key": _normalize_field_key(missing_labels[0]),
                "missing_fields": missing_labels,
                "question_en": msg_en,
                "question_hi": msg_hi,
            },
            checkpoint_saved=True,
            current_step_index=inspect_out.current_step_number,
            total_steps=inspect_out.total_steps,
            status=task.status,
        )

    await audit_log_tool_call(
        db,
        task.id,
        "map_form_fields",
        "success",
        map_in.model_dump(),
        map_out.model_dump(),
    )

    fill_in = FillFormFieldsInput(mappings=all_mappings)
    fill_out = await fill_form_fields_tool(fill_in)
    await audit_log_tool_call(
        db,
        task.id,
        "fill_form_fields",
        "success",
        fill_in.model_dump(),
        fill_out.model_dump(),
    )

    task.status = TaskState.FILLING.value
    task.current_step_index = inspect_out.current_step_number
    task.total_steps = inspect_out.total_steps
    task.checkpoint_state = {
        **checkpoint_data,
        "step": inspect_out.current_step_number,
        "step_name": inspect_out.active_step,
        "url": url,
        "mapped_fields_count": len(all_mappings),
        "sensitive_fields_count": map_out.sensitive_fields_count,
    }
    await db.flush()

    msg_en = (
        f"Mapped and prepared {len(fill_out.actions)} fields for {inspect_out.active_step} "
        f"({map_out.sensitive_fields_count} sensitive fields protected with local vault tokens)."
    )
    msg_hi = (
        f"{inspect_out.active_step} के लिए {len(fill_out.actions)} फ़ील्ड तैयार किए गए "
        f"({map_out.sensitive_fields_count} संवेदनशील फ़ील्ड स्थानीय वॉल्ट टोकन से सुरक्षित हैं)।"
    )

    return AgentStepResponse(
        task_id=task.id,
        response_type="action",
        intent_lane="form_automation",
        message=msg_hi if lang == "hi" else msg_en,
        message_hi=msg_hi,
        next_tool="fill_form_fields",
        client_actions=fill_out.actions,
        checkpoint_saved=True,
        current_step_index=inspect_out.current_step_number,
        total_steps=inspect_out.total_steps,
        status=task.status,
    )
