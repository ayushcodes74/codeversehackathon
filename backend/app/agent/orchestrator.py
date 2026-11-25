"""AI Agent Orchestrator for Universal AI Web Assistant.

Implements the Observe -> Plan -> Select Tool -> Execute -> Verify state machine:
- Two Intent Lanes:
  1. Mid-Form Knowledge / Visual Guide Q&A (non-destructive, keeps active form state)
  2. Form Automation Actions (Observe DOM, Domain Shield, Fill Fields, Gate on Submit, Capture Receipt)
- Safe-Pause & Resume Later (persisting checkpoint_state into tasks table)
- Zero-Leak Hybrid Storage (emitting vault references instead of plain digits)
- Full Tool Audit Logging with redaction.
"""

from __future__ import annotations
import uuid
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models import Task, User, FamilyProfile, AgentMemory
from app.schemas.agent_contracts import (
    AgentChatRequest,
    AgentStepResponse,
    ClientAction,
    UnderstandUserGoalInput,
    InspectPageInput,
    MapFormFieldsInput,
    FillFormFieldsInput,
    RequestApprovalInput,
    SaveTaskStateInput,
    CaptureReceiptInput,
)
from app.agent.discovery import check_domain_shield, discover_or_narrow_portal
from app.agent.visual_guides import is_visual_guide_query, get_visual_guide_card
from app.agent.tool_registry import (
    understand_user_goal_tool,
    inspect_page_tool,
    map_form_fields_tool,
    fill_form_fields_tool,
    request_approval_tool,
    save_task_state_tool,
    capture_receipt_tool,
    audit_log_tool_call,
)
from app.llm.client import get_llm_client, UnconfiguredLLMClient
from app.llm.types import LLMRequest
from app.llm.exceptions import LLMError, LLMNotConfiguredError


async def enhance_with_llm(
    user_msg: str,
    default_msg_en: str,
    default_msg_hi: str,
    language: str = "en",
    context: str = ""
) -> tuple[str, str]:
    """Uses Anubhav's LLM client to refine natural-language bilingual responses when configured,
    gracefully falling back to rule-based templates if no provider or API key is set."""
    try:
        llm = get_llm_client()
        if isinstance(llm, UnconfiguredLLMClient):
            return default_msg_en, default_msg_hi

        prompt = (
            f"You are an assistant for Indian government portal automation.\n"
            f"User message: {user_msg}\n"
            f"Context: {context}\n"
            f"Respond with a clear, concise instruction in English and Hindi.\n"
            f"Format as: EN: <english_text> | HI: <hindi_text>"
        )
        resp = await llm.generate(
            LLMRequest(
                prompt=prompt,
                system_instruction="You are an empathetic, bilingual web automation assistant.",
                temperature=0.2,
                timeout_seconds=5.0
            )
        )
        raw = resp.raw_text.strip()
        if "EN:" in raw and "HI:" in raw:
            parts = raw.split("HI:", 1)
            en_part = parts[0].replace("EN:", "").strip().rstrip("|").strip()
            hi_part = parts[1].strip()
            return en_part or default_msg_en, hi_part or default_msg_hi
    except (LLMNotConfiguredError, LLMError, Exception):
        pass

    return default_msg_en, default_msg_hi


async def get_or_create_task(
    db: AsyncSession,
    user_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    task_id: uuid.UUID | None,
    goal_text: str,
    target_url: str | None = None
) -> Task:
    """Finds an existing in-progress task or creates a new one in PostgreSQL."""
    # 1. By explicit task_id
    if task_id:
        stmt = select(Task).where(Task.id == task_id)
        res = await db.execute(stmt)
        task = res.scalar_one_or_none()
        if task:
            if target_url and not task.target_url:
                task.target_url = target_url
            return task

    # 2. Find most recent active task for this user and profile
    stmt = (
        select(Task)
        .where(Task.user_id == user_id, Task.status.in_(["in_progress", "paused"]))
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

    # 3. Create new task
    # Ensure profile_id is valid or fallback to default 'self' profile
    resolved_profile_id = profile_id
    if not resolved_profile_id:
        prof_res = await db.execute(
            select(FamilyProfile).where(FamilyProfile.user_id == user_id, FamilyProfile.is_default == True)  # noqa: E712
        )
        def_prof = prof_res.scalar_one_or_none()
        if def_prof:
            resolved_profile_id = def_prof.id
        else:
            # Fallback to any profile
            any_prof_res = await db.execute(select(FamilyProfile).where(FamilyProfile.user_id == user_id))
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
        status="in_progress",
        current_step_index=1,
        total_steps=4,
        checkpoint_state={"step": 1, "history": []}
    )
    db.add(new_task)
    await db.flush()
    return new_task


async def fetch_profile_memories(
    db: AsyncSession,
    user_id: uuid.UUID,
    profile_id: uuid.UUID
) -> tuple[str, dict[str, str]]:
    """Retrieves profile slug and all stored memory facts for the profile."""
    # Profile slug
    p_stmt = select(FamilyProfile).where(FamilyProfile.id == profile_id)
    p_res = await db.execute(p_stmt)
    prof = p_res.scalar_one_or_none()
    slug = prof.profile_slug if prof else "self"

    # Memories
    m_stmt = select(AgentMemory).where(
        AgentMemory.user_id == user_id,
        AgentMemory.profile_id == profile_id
    )
    m_res = await db.execute(m_stmt)
    memories: dict[str, str] = {}
    for m in m_res.scalars().all():
        memories[m.fact_key.lower()] = m.fact_value

    return slug, memories


async def execute_agent_turn(
    request: AgentChatRequest,
    db: AsyncSession
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
            # Fetch active task if exists to maintain task continuity
            task_id = request.task_id
            if not task_id:
                stmt = (
                    select(Task.id)
                    .where(Task.user_id == request.user_id, Task.status.in_(["in_progress", "paused"]))
                    .order_by(desc(Task.updated_at))
                )
                res = await db.execute(stmt)
                task_id = res.scalar_one_or_none()

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
                status="in_progress"
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
                status="paused"
            )

    # 2. Get or Create Task Checkpoint
    task = await get_or_create_task(
        db=db,
        user_id=request.user_id,
        profile_id=request.profile_id,
        task_id=request.task_id,
        goal_text=user_msg,
        target_url=url
    )

    # 3. If no DOM snapshot is provided, understand goal or navigate to portal
    if not dom:
        # Check if portal needs discovery or narrowing
        portal_item, clarify_q, disc_msg = await discover_or_narrow_portal(
            user_id=request.user_id,
            profile_id=task.profile_id,
            goal_text=task.goal_text or user_msg,
            language=lang,
            db=db
        )

        if clarify_q:
            # User clarification needed (domicile state, education level)
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
                status=task.status
            )

        if portal_item:
            task.portal_id = portal_item.id
            task.target_url = portal_item.entry_url
            await db.flush()

            nav_action = ClientAction(
                action_type="NAVIGATE",
                value_or_vault_ref=portal_item.entry_url,
                description=f"Navigating to {portal_item.portal_name}"
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
                status="in_progress"
            )

        ready_en, ready_hi = await enhance_with_llm(
            user_msg=user_msg,
            default_msg_en="Ready. Please open the portal or provide the page URL.",
            default_msg_hi="तैयार। कृपया पोर्टल खोलें या पेज URL प्रदान करें।",
            language=lang,
            context="Initial portal navigation state"
        )
        return AgentStepResponse(
            task_id=task.id,
            response_type="action",
            intent_lane="form_automation",
            message=ready_hi if lang == "hi" else ready_en,
            message_hi=ready_hi,
            status="in_progress"
        )

    # 4. Observe: Inspect DOM Snapshot
    inspect_in = InspectPageInput(dom_snapshot=dom, current_url=url)
    inspect_out = await inspect_page_tool(inspect_in)
    await audit_log_tool_call(db, task.id, "inspect_page", "success", inspect_in.model_dump(), inspect_out.model_dump())

    task.current_step_index = inspect_out.current_step_number
    task.total_steps = inspect_out.total_steps

    # Case A: Post-Submission Receipt / Acknowledgement Screen
    if inspect_out.page_type == "acknowledgement_receipt":
        receipt_in = CaptureReceiptInput(dom_snapshot=dom)
        receipt_out = await capture_receipt_tool(receipt_in)
        await audit_log_tool_call(db, task.id, "capture_receipt", "success", receipt_in.model_dump(), receipt_out.model_dump())

        task.application_ref_no = receipt_out.receipt_number
        task.status = "completed"
        task.checkpoint_state = {
            "completed": True,
            "application_ref_no": receipt_out.receipt_number,
            "receipt": receipt_out.model_dump()
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
            status="completed"
        )

    # Case B: Human-in-the-Loop Gate on Review & Final Submit Step
    is_review_step = (
        inspect_out.page_type == "review_page" or
        "step 4" in (dom.step_indicator or "").lower() or
        any("submit" in b.text.lower() for b in dom.buttons)
    )

    # If the user explicitly approved, proceed
    has_user_approved = any(w in user_msg.lower() for w in ["approve", "approved", "confirm submit", "yes proceed"])

    if is_review_step and not has_user_approved:
        # Find submit button selector
        submit_btn = next((b for b in dom.buttons if "submit" in b.text.lower()), None)
        btn_sel = submit_btn.selector if submit_btn else "#btn-final-submit"
        btn_txt = submit_btn.text if submit_btn else "Submit Application"

        req_approval_in = RequestApprovalInput(
            action_type="submit_form",
            summary_of_changes={f.label or f.element_key: f.current_value or "[Pending]" for f in dom.fields[:6]},
            button_selector=btn_sel,
            button_text=btn_txt,
            warning_message_en="Human Approval Required: Please verify all filled details before final submission. Antigravity will never auto-submit government applications.",
            warning_message_hi="मानवीय स्वीकृति आवश्यक: कृपया अंतिम जमा करने से पहले सभी भरे हुए विवरणों की पुष्टि करें। हम कभी भी स्वचालित रूप से अंतिम आवेदन जमा नहीं करते।"
        )
        approval_out = await request_approval_tool(req_approval_in)
        await audit_log_tool_call(db, task.id, "request_approval", "success", req_approval_in.model_dump(), approval_out.model_dump())

        # Update checkpoint state to paused for approval
        task.status = "paused"
        task.checkpoint_state = {
            "awaiting_approval": True,
            "step": inspect_out.current_step_number,
            "summary": req_approval_in.summary_of_changes
        }
        await db.flush()

        msg_en = "Step 4 Review complete. Human approval required before clicking final Submit."
        msg_hi = "चरण 4 समीक्षा पूर्ण। अंतिम सबमिट करने से पहले मानवीय स्वीकृति आवश्यक है।"

        return AgentStepResponse(
            task_id=task.id,
            response_type="approval_request",
            intent_lane="form_automation",
            message=msg_hi if lang == "hi" else msg_en,
            message_hi=msg_hi,
            next_tool="request_approval",
            approval_request=approval_out,
            client_actions=[],  # Zero click actions emitted!
            checkpoint_saved=True,
            current_step_index=inspect_out.current_step_number,
            total_steps=inspect_out.total_steps,
            status="paused"
        )

    # Case C: Form Step Automation (Map & Fill Fields)
    profile_slug, memories = await fetch_profile_memories(db, task.user_id, task.profile_id)

    map_in = MapFormFieldsInput(
        fields=dom.fields,
        profile_slug=profile_slug,
        available_memories=memories
    )
    map_out = await map_form_fields_tool(map_in)
    await audit_log_tool_call(db, task.id, "map_form_fields", "success", map_in.model_dump(), map_out.model_dump())

    fill_in = FillFormFieldsInput(mappings=map_out.mappings)
    fill_out = await fill_form_fields_tool(fill_in)
    await audit_log_tool_call(db, task.id, "fill_form_fields", "success", fill_in.model_dump(), fill_out.model_dump())

    # Save Checkpoint State in PostgreSQL (Safe-Pause & Resume Later)
    task.current_step_index = inspect_out.current_step_number
    task.total_steps = inspect_out.total_steps
    task.checkpoint_state = {
        "step": inspect_out.current_step_number,
        "step_name": inspect_out.active_step,
        "url": url,
        "mapped_fields_count": len(map_out.mappings),
        "sensitive_fields_count": map_out.sensitive_fields_count
    }
    await db.flush()

    msg_en = f"Mapped and prepared {len(fill_out.actions)} fields for {inspect_out.active_step} ({map_out.sensitive_fields_count} sensitive fields protected with local vault tokens)."
    msg_hi = f"{inspect_out.active_step} के लिए {len(fill_out.actions)} फ़ील्ड तैयार किए गए ({map_out.sensitive_fields_count} संवेदनशील फ़ील्ड स्थानीय वॉल्ट टोकन से सुरक्षित हैं)।"

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
        status="in_progress"
    )
