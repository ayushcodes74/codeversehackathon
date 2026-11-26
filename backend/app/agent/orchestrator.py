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
    FillFormFieldsInput,
    InspectPageInput,
    MapFormFieldsInput,
    RequestApprovalInput,
)

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

    # 3. If no DOM snapshot is provided, discover/narrow portal
    if not dom:
        portal_item, clarify_q, disc_msg = await discover_or_narrow_portal(
            user_id=request.user_id,
            profile_id=task.profile_id,
            goal_text=task.goal_text or user_msg,
            language=lang,
            db=db,
        )

        if clarify_q:
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

        if portal_item:
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

    # Case A: Post-Submission Receipt / Acknowledgement Screen (PRD FR-061)
    if inspect_out.page_type == "acknowledgement_receipt":
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
    is_review_step = (
        inspect_out.page_type == "review_page"
        or "step 4" in (dom.step_indicator or "").lower()
        or any("submit" in b.text.lower() for b in dom.buttons)
    )

    has_verified_approval = await _has_verified_submit_approval(db, task.id)
    if is_review_step and not has_verified_approval:
        submit_btn = next((b for b in dom.buttons if "submit" in b.text.lower()), None)
        btn_sel = submit_btn.selector if submit_btn else "#btn-final-submit"
        btn_txt = submit_btn.text if submit_btn else "Submit Application"

        summary_changes = redact_dict(
            {f.label or f.element_key: f.current_value or "[Pending]" for f in dom.fields[:6]}
        )
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

    map_in = MapFormFieldsInput(
        fields=dom.fields,
        profile_slug=profile_slug,
        available_memories=memories,
    )
    map_out = await map_form_fields_tool(map_in)
    await audit_log_tool_call(
        db,
        task.id,
        "map_form_fields",
        "success",
        map_in.model_dump(),
        map_out.model_dump(),
    )

    fill_in = FillFormFieldsInput(mappings=map_out.mappings)
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
        "step": inspect_out.current_step_number,
        "step_name": inspect_out.active_step,
        "url": url,
        "mapped_fields_count": len(map_out.mappings),
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
