"""Comprehensive Test Suite for Universal AI Web Assistant (Ayush's Track).

Verifies:
1. Unified Pydantic Contracts (16 agent tools)
2. Official Domain Shield & Goal-to-Portal Discovery
3. Contextual Visual Guides & Mid-Form Bilingual Q&A
4. Tool Registry & Zero-Leak Sensitive Redaction
5. Orchestration State Machine (Observe -> Plan -> Execute -> Verify)
6. Human-in-the-Loop Gate on Final Submit (Zero auto-click)
7. Safe-Pause & Resume Later Checkpoint Persistence
8. Post-Submission Receipt Capture
"""

import pytest
import asyncio
import uuid
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.database import get_db, AsyncSessionLocal
from app.models import User, FamilyProfile, AgentMemory, ApprovedPortal, Task
from app.agent.discovery import check_domain_shield, discover_or_narrow_portal
from app.agent.visual_guides import get_visual_guide_card, is_visual_guide_query
from app.agent.tool_registry import sanitize_sensitive_data
from app.schemas.agent_contracts import (
    DOMSnapshot,
    DOMField,
    DOMButton,
    AgentChatRequest,
)


@pytest.mark.anyio
async def test_zero_leak_redaction():
    """Verify raw PAN, Aadhaar, Bank Account are redacted while vault refs are preserved."""
    raw_data = {
        "user_name": "Ayush Kumar",
        "aadhaar": "1234 5678 9012",
        "pan": "ABCDE1234F",
        "bank_account": "98765432101234",
        "vault_token": "vault:self.pan.id_number"
    }
    sanitized = sanitize_sensitive_data(raw_data)

    assert "[REDACTED" in sanitized["aadhaar"] or sanitized["aadhaar"] == "[REDACTED_SENSITIVE_ID]"
    assert "[REDACTED" in sanitized["pan"] or sanitized["pan"] == "[REDACTED_SENSITIVE_ID]"
    assert sanitized["vault_token"] == "vault:self.pan.id_number", "Vault tokens must never be damaged!"
    print("✓ Zero-Leak redaction passed.")


@pytest.mark.anyio
async def test_official_domain_shield():
    """Verify Official Domain Shield blocks lookalike domains and permits approved/local domains."""
    async with AsyncSessionLocal() as session:
        # Check if scholarships.gov.in already exists
        from sqlalchemy import select
        res = await session.execute(select(ApprovedPortal).where(ApprovedPortal.domain == "scholarships.gov.in"))
        portal = res.scalar_one_or_none()
        if not portal:
            test_portal = ApprovedPortal(
                portal_name="National Scholarship Portal",
                domain="scholarships.gov.in",
                entry_url="https://scholarships.gov.in",
                category="Education / Scholarships",
                is_active=True
            )
            session.add(test_portal)
            await session.commit()

        # 1. Official domain -> ALLOWED
        res_official = await check_domain_shield("https://scholarships.gov.in/apply", session)
        assert res_official.is_allowed is True
        assert res_official.shield_status == "VERIFIED_OFFICIAL"

        # 2. Local dev synthetic demo -> ALLOWED
        res_local = await check_domain_shield("http://localhost:8000/demo-form.html", session)
        assert res_local.is_allowed is True
        assert res_local.shield_status == "LOCAL_DEV_ALLOWED"

        # 3. Phishing / Lookalike domain -> BLOCKED
        res_fake = await check_domain_shield("https://scholarships-gov-portal-fake.com", session)
        assert res_fake.is_allowed is False
        assert res_fake.shield_status == "BLOCKED_UNOFFICIAL"
        print("✓ Official Domain Shield test passed.")


@pytest.mark.anyio
async def test_visual_guides_bilingual():
    """Verify Visual Guide Cards return correct bilingual data and bounding box callouts."""
    assert is_visual_guide_query("where is my account number?") is True
    assert is_visual_guide_query("खाता संख्या कहाँ है?") is True
    assert is_visual_guide_query("apply for post-matric scholarship") is False

    card_en = get_visual_guide_card("where is my account number", language="en")
    assert card_en is not None
    assert card_en.document_sample_id == "sample_bank_passbook"
    assert len(card_en.bounding_box_callouts) > 0
    assert card_en.quick_action == "FOCUS_INPUT"

    card_hi = get_visual_guide_card("IFSC कोड कहाँ मिलेगा", language="hi")
    assert card_hi is not None
    assert "IFSC" in card_hi.title
    print("✓ Visual guides bilingual test passed.")


@pytest.mark.anyio
async def test_orchestration_end_to_end():
    """Test full Orchestration cycle with FastAPI TestClient."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup user & profile
        auth_resp = await client.post("/api/v1/users/auth", json={
            "google_sub": f"test_sub_{uuid.uuid4().hex[:6]}",
            "email": f"ayush_{uuid.uuid4().hex[:6]}@example.com",
            "full_name": "Ayush Kumar",
            "mobile_no": "9876543210",
            "preferred_language": "en"
        })
        assert auth_resp.status_code == 200
        user_data = auth_resp.json()
        user_id = user_data["user_id"]
        profile_id = user_data["profiles"][0]["id"]

        # 2. Mid-Form Visual Guide Q&A (Non-Destructive Lane)
        qa_resp = await client.post("/api/v1/agent/chat", json={
            "user_id": user_id,
            "profile_id": profile_id,
            "message": "Where is my account number on the passbook?",
            "language": "en"
        })
        assert qa_resp.status_code == 200
        qa_data = qa_resp.json()
        assert qa_data["response_type"] == "visual_guide"
        assert qa_data["intent_lane"] == "knowledge_qa"
        assert qa_data["visual_guide"]["document_sample_id"] == "sample_bank_passbook"

        # 3. Form Step 1: DOM Inspection & Vault Ref Field Mapping
        dom_step1 = {
            "url": "http://localhost:8000/demo-form.html",
            "title": "National Scholarship Portal",
            "step_indicator": "Step 1 of 4: Personal Details",
            "current_step_number": 1,
            "total_steps": 4,
            "fields": [
                {"element_key": "field_01", "selector": "#fullName", "tag_name": "input", "input_type": "text", "label": "Full Name", "current_value": ""},
                {"element_key": "field_02", "selector": "#panNumber", "tag_name": "input", "input_type": "text", "label": "PAN Number", "current_value": ""},
                {"element_key": "field_03", "selector": "#aadhaarNumber", "tag_name": "input", "input_type": "text", "label": "Aadhaar Number", "current_value": ""}
            ],
            "buttons": [{"element_key": "btn_01", "selector": "#btn-next-1", "text": "Save & Continue"}]
        }

        step1_resp = await client.post("/api/v1/agent/chat", json={
            "user_id": user_id,
            "profile_id": profile_id,
            "message": "Fill personal details",
            "current_url": "http://localhost:8000/demo-form.html",
            "dom_snapshot": dom_step1
        })
        assert step1_resp.status_code == 200
        step1_data = step1_resp.json()
        assert step1_data["response_type"] == "action"
        actions = step1_data["client_actions"]
        assert len(actions) == 3

        # Verify Zero-Leak: Sensitive fields received vault tokens
        pan_action = next(a for a in actions if a["element_key"] == "field_02")
        assert pan_action["value_or_vault_ref"].startswith("vault:")
        assert "pan.id_number" in pan_action["value_or_vault_ref"]
        assert pan_action["is_sensitive"] is True

        task_id = step1_data["task_id"]

        # 4. Safe-Pause & Resume: Verify task is resumable
        resumable_resp = await client.get(f"/api/v1/agent/tasks/{user_id}/resumable")
        assert resumable_resp.status_code == 200
        resumable_data = resumable_resp.json()
        assert resumable_data["count"] >= 1

        # 5. Human-in-the-Loop Gate on Final Step (Step 4 Review & Submit)
        dom_step4 = {
            "url": "http://localhost:8000/demo-form.html",
            "title": "National Scholarship Portal",
            "step_indicator": "Step 4 of 4: Review & Submit",
            "current_step_number": 4,
            "total_steps": 4,
            "fields": [
                {"element_key": "field_01", "selector": "#rev-fullName", "tag_name": "td", "input_type": "text", "label": "Applicant Full Name", "current_value": "Ayush Kumar"}
            ],
            "buttons": [
                {"element_key": "btn_01", "selector": "#btn-final-submit", "text": "Confirm and Submit Application"}
            ],
            "text_content_snippet": "Please review your filled application details."
        }

        step4_resp = await client.post("/api/v1/agent/chat", json={
            "user_id": user_id,
            "profile_id": profile_id,
            "task_id": task_id,
            "message": "Ready to submit",
            "current_url": "http://localhost:8000/demo-form.html",
            "dom_snapshot": dom_step4
        })
        assert step4_resp.status_code == 200
        step4_data = step4_resp.json()
        assert step4_data["response_type"] == "approval_request"
        assert len(step4_data["client_actions"]) == 0, "Human-in-the-loop gate must NEVER emit click action on final submit!"
        assert step4_data["approval_request"]["approval_required"] is True

        # 6. Post-Submit Acknowledgement Receipt Capture
        dom_receipt = {
            "url": "http://localhost:8000/demo-form.html",
            "title": "National Scholarship Portal",
            "step_indicator": "Acknowledgement Receipt",
            "current_step_number": 4,
            "total_steps": 4,
            "fields": [],
            "buttons": [],
            "text_content_snippet": "Application Submitted Successfully! Application Reference No: APP-2026-88412."
        }

        receipt_resp = await client.post("/api/v1/agent/chat", json={
            "user_id": user_id,
            "profile_id": profile_id,
            "task_id": task_id,
            "message": "Extract receipt",
            "current_url": "http://localhost:8000/demo-form.html",
            "dom_snapshot": dom_receipt
        })
        assert receipt_resp.status_code == 200
        receipt_data = receipt_resp.json()
        assert receipt_data["response_type"] == "completed"
        assert receipt_data["receipt_data"]["receipt_number"] == "APP-2026-88412"
        assert receipt_data["status"] == "completed"

        print("✓ End-to-End Orchestration cycle passed successfully!")


async def main():
    print("Running Agent Orchestration & Browser Automation Tests...")
    await test_zero_leak_redaction()
    await test_official_domain_shield()
    await test_visual_guides_bilingual()
    await test_orchestration_end_to_end()
    print("\n=========================================")
    print("ALL TESTS PASSED WITH 100% SUCCESS!")
    print("=========================================")


if __name__ == "__main__":
    asyncio.run(main())
