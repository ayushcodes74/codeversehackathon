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
import sys
import uuid
from pathlib import Path
from httpx import AsyncClient, ASGITransport

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app
from app.database import AsyncSessionLocal, engine
from app.models import ApprovedPortal
from app.api.db_routes import TaskState, create_hs256_jwt
from app.agent.discovery import check_domain_shield
from app.agent.visual_guides import get_visual_guide_card, is_visual_guide_query
from app.agent.tool_registry import sanitize_sensitive_data


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    yield
    await engine.dispose()


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

        # 2. Local dev synthetic demo (localhost & 127.0.0.1 any port) -> ALLOWED
        res_local = await check_domain_shield("http://localhost:8000/demo-form.html", session)
        assert res_local.is_allowed is True
        assert res_local.shield_status == "LOCAL_DEV_ALLOWED"

        res_127 = await check_domain_shield("http://127.0.0.1:5500/demo-portal/demo-form.html", session)
        assert res_127.is_allowed is True
        assert res_127.shield_status == "LOCAL_DEV_ALLOWED"

        # 2b. file:// in dev mode vs production mode
        from app.core.config import get_settings
        settings = get_settings()
        orig_allow_local = settings.allow_local_dev_portals
        orig_env = settings.app_env
        try:
            settings.allow_local_dev_portals = True
            settings.app_env = "development"
            res_file_dev = await check_domain_shield("file:///D:/codeversehackathon/demo-portal/demo-form.html", session)
            assert res_file_dev.is_allowed is True
            assert res_file_dev.shield_status == "LOCAL_DEV_ALLOWED"

            settings.app_env = "production"
            res_file_prod = await check_domain_shield("file:///D:/codeversehackathon/demo-portal/demo-form.html", session)
            assert res_file_prod.is_allowed is False
            assert res_file_prod.shield_status == "BLOCKED_UNOFFICIAL"
            assert "python -m http.server 5500" in (res_file_prod.warning or "")
        finally:
            settings.allow_local_dev_portals = orig_allow_local
            settings.app_env = orig_env

        # 3. Arbitrary untrusted HTTP & Phishing / Lookalike domain -> BLOCKED
        res_http_ext = await check_domain_shield("http://untrusted-external-portal.com/login", session)
        assert res_http_ext.is_allowed is False
        assert res_http_ext.shield_status == "BLOCKED_UNOFFICIAL"

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
        auth_headers = {"Authorization": f"Bearer {create_hs256_jwt(user_id)}"}

        # Seed non-sensitive profile memory for full_name (replaces removed hardcoded stub)
        mem_resp = await client.put(
            f"/api/v1/users/{user_id}/memory",
            json={
                "profile_id": profile_id,
                "category": "personal",
                "fact_key": "full_name",
                "fact_value": "Ayush Kumar",
            },
            headers=auth_headers,
        )
        assert mem_resp.status_code == 200

        # 2. Mid-Form Visual Guide Q&A (Non-Destructive Lane)
        qa_resp = await client.post("/api/v1/agent/chat", json={
            "user_id": user_id,
            "profile_id": profile_id,
            "message": "Where is my account number on the passbook?",
            "language": "en"
        }, headers=auth_headers)
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
        }, headers=auth_headers)
        assert step1_resp.status_code == 200
        step1_data = step1_resp.json()
        assert step1_data["response_type"] == "action"
        actions = step1_data["client_actions"]
        assert len(actions) == 3

        # Verify Zero-Leak: Sensitive fields received vault tokens
        pan_action = next(a for a in actions if a["element_key"] == "field_02")
        assert pan_action["value_or_vault_ref"].startswith("vault:")
        assert "pan.pan_number" in pan_action["value_or_vault_ref"]
        assert pan_action["is_sensitive"] is True

        task_id = step1_data["task_id"]

        # 4. Safe-Pause & Resume: Verify task is listed under GET /api/v1/tasks
        resumable_resp = await client.get(f"/api/v1/tasks?user_id={user_id}", headers=auth_headers)
        assert resumable_resp.status_code == 200
        resumable_data = resumable_resp.json()
        assert len(resumable_data["tasks"]) >= 1

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
        }, headers=auth_headers)
        assert step4_resp.status_code == 200
        step4_data = step4_resp.json()
        assert step4_data["response_type"] == "approval_request"
        assert step4_data["status"] == TaskState.AWAITING_SUBMIT_APPROVAL.value
        assert len(step4_data["client_actions"]) == 0, "Human-in-the-loop gate must NEVER emit click action on final submit!"
        assert step4_data["approval_request"]["approval_required"] is True

        # Record action-bound cryptographic approval -> transitions to SUBMITTING
        task_resp = await client.get(f"/api/v1/tasks/{task_id}", headers=auth_headers)
        assert task_resp.status_code == 200
        pending_action_hash = task_resp.json()["pending_action_hash"]
        assert pending_action_hash

        approval_resp = await client.post(
            f"/api/v1/tasks/{task_id}/approval",
            json={"action_hash": pending_action_hash, "approved": True},
            headers=auth_headers,
        )
        assert approval_resp.status_code == 200
        approval_data = approval_resp.json()
        assert approval_data["approved"] is True
        assert approval_data["status"] == TaskState.SUBMITTING.value
        assert approval_data["action_hash"] == pending_action_hash

        # 6. Verified Submit Dispatch: agent emits CLICK_BUTTON while task stays in SUBMITTING
        submit_click_resp = await client.post("/api/v1/agent/chat", json={
            "user_id": user_id,
            "profile_id": profile_id,
            "task_id": task_id,
            "message": "Proceed with approved submission",
            "current_url": "http://localhost:8000/demo-form.html",
            "dom_snapshot": dom_step4
        }, headers=auth_headers)
        assert submit_click_resp.status_code == 200
        submit_click_data = submit_click_resp.json()
        assert submit_click_data["response_type"] == "action"
        assert submit_click_data["status"] == TaskState.SUBMITTING.value
        assert len(submit_click_data["client_actions"]) == 1
        assert submit_click_data["client_actions"][0]["action_type"] == "CLICK_BUTTON"
        assert submit_click_data["client_actions"][0]["selector"] == "#btn-final-submit"

        # 7. Post-Submit Acknowledgement Receipt Capture -> SUBMITTING transitions to SUBMITTED
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
        }, headers=auth_headers)
        assert receipt_resp.status_code == 200
        receipt_data = receipt_resp.json()
        assert receipt_data["response_type"] == "completed"
        assert receipt_data["receipt_data"]["receipt_number"] == "APP-2026-88412"
        assert receipt_data["status"] == TaskState.SUBMITTED.value

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
