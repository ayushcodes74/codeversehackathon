"""Comprehensive PRD Gap Integration Tests.

Validates:
1. LLM Client Structured Tool Calling vs Heuristic Fallback (FR-010, FR-011)
2. Versioned WorkflowDefinition Registry & DB Lookup (FR-010)
3. Domain Shield allowed_paths Governance & HTTP Redirect Chain Blocking (FR-012, FR-013)
4. Synthetic Annotated Visual Guide Generation with PIL Base64 PNG (FR-051)
"""

from __future__ import annotations
import io
import sys
import uuid
import base64
import pytest
from pathlib import Path
from PIL import Image
import httpx
from httpx import AsyncClient, ASGITransport

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app
from app.database import AsyncSessionLocal, engine, init_db_schema
from app.models import ApprovedPortal, WorkflowDefinition
from app.api.db_routes import create_hs256_jwt
from app.agent.discovery import check_domain_shield
from app.agent.visual_guides import get_visual_guide_card
from app.agent.tool_registry import understand_user_goal_tool, generate_task_plan_tool
from app.schemas.agent_contracts import (
    UnderstandUserGoalInput,
    GenerateTaskPlanInput,
)
from app.llm.client import MockLLMClient, UnconfiguredLLMClient
from app.llm.types import LLMResponse


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests():
    await init_db_schema()
    yield
    await engine.dispose()


@pytest.mark.anyio
async def test_llm_tool_calling_and_heuristic_fallback():
    """Verify structured LLM goal understanding and automatic heuristic fallback (FR-010, FR-011)."""
    # 1. Structured Tool Calling with MockLLMClient
    structured_json = (
        '{"intent": "form_automation", "detected_language": "en", '
        '"extracted_entities": {"scheme": "national_scholarship", "education_level": "Post-Matric"}, '
        '"requires_clarification": false, "confidence": 0.98}'
    )
    mock_client = MockLLMClient(canned_response=structured_json)
    input_payload = UnderstandUserGoalInput(
        user_message="I want to apply for post-matric national scholarship",
        language="en"
    )

    result_llm = await understand_user_goal_tool(input_payload, llm_client=mock_client)
    assert result_llm.intent == "form_automation"
    assert result_llm.detected_language == "en"
    assert result_llm.confidence == 0.98
    assert result_llm.extracted_entities.get("scheme") == "national_scholarship"

    # 2. Heuristic fallback when LLM is unconfigured / unavailable
    unconfigured_client = UnconfiguredLLMClient()
    result_fallback = await understand_user_goal_tool(input_payload, llm_client=unconfigured_client)
    assert result_fallback.intent == "form_automation"
    assert result_fallback.detected_language == "en"
    assert result_fallback.extracted_entities.get("scheme_type") == "scholarship"
    assert result_fallback.extracted_entities.get("education_level") == "Post-Matric"
    print("✓ LLM structured tool calling and heuristic fallback passed.")


@pytest.mark.anyio
async def test_workflow_definition_registry_and_versioning():
    """Verify CRUD, versioning and DB step resolution for WorkflowDefinition (FR-010)."""
    user_id = str(uuid.uuid4())
    token = create_hs256_jwt({"sub": user_id, "user_id": user_id, "role": "authenticated"})
    auth_headers = {
        "Authorization": f"Bearer {token}",
        "x-dev-user-id": user_id,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        wf_slug = f"nsp_post_matric_{uuid.uuid4().hex[:6]}"
        # Create Version 1
        create_payload = {
            "workflow_id": wf_slug,
            "name": f"National Scholarship Post-Matric {wf_slug}",
            "version": "1.0.0",
            "description": "Post-Matric Scholarship Automation Workflow",
            "steps": [
                {"step_index": 1, "step_name": "Personal Details", "description": "Fill personal identity fields"},
                {"step_index": 2, "step_name": "Academic Details", "description": "Fill course and college information"},
                {"step_index": 3, "step_name": "Bank & Aadhaar", "description": "Provide bank passbook details"},
                {"step_index": 4, "step_name": "Review & Final Submit", "description": "Human review and approval"}
            ]
        }
        res1 = await client.post("/api/v1/workflows", json=create_payload, headers=auth_headers)
        assert res1.status_code == 201
        wf1_data = res1.json()
        wf_id_db = wf1_data["id"]
        assert wf1_data["version"] == "1.0.0"
        assert len(wf1_data["steps"]) == 4

        # List workflows
        list_res = await client.get("/api/v1/workflows", headers=auth_headers)
        assert list_res.status_code == 200
        assert any(w["id"] == wf_id_db for w in list_res.json())

        # Create Version 2 of the same workflow
        create_v2_payload = {
            "workflow_id": wf_slug,
            "name": f"National Scholarship Post-Matric {wf_slug} v2",
            "version": "2.0.0",
            "description": "Updated 5-step workflow",
            "steps": [
                {"step_index": 1, "step_name": "Student Profile", "description": "Verify student demographic info"},
                {"step_index": 2, "step_name": "Institution Details", "description": "Select accredited college"},
                {"step_index": 3, "step_name": "Fee Receipts", "description": "Upload current academic fee receipts"},
                {"step_index": 4, "step_name": "Bank & Direct Benefit Transfer", "description": "Link bank details"},
                {"step_index": 5, "step_name": "Action-Bound Review & Submit", "description": "Mandatory human review"}
            ]
        }
        res2 = await client.post("/api/v1/workflows", json=create_v2_payload, headers=auth_headers)
        assert res2.status_code == 201
        wf2_data = res2.json()
        assert wf2_data["version"] == "2.0.0"
        assert len(wf2_data["steps"]) == 5

        # Query tool plan generation using database lookup
        async with AsyncSessionLocal() as session:
            plan_res = await generate_task_plan_tool(
                GenerateTaskPlanInput(
                    portal_name=wf_slug,
                    portal_url="https://scholarships.gov.in",
                    goal_text="Apply for NSP Post Matric scholarship"
                ),
                db=session
            )
            assert plan_res.total_steps == 5
            assert plan_res.steps[0].step_name == "Student Profile"
            assert plan_res.plan_id.endswith("_v2.0.0")

        # Deactivate Version 1
        del_res = await client.delete(f"/api/v1/workflows/{wf_id_db}", headers=auth_headers)
        assert del_res.status_code == 200
        assert del_res.json()["is_active"] is False

    print("✓ Versioned WorkflowDefinition registry and lookup passed.")


@pytest.mark.anyio
async def test_domain_shield_allowed_paths_and_redirects():
    """Verify Domain Shield path restriction (FR-012) and redirect chain validation (FR-013)."""
    async with AsyncSessionLocal() as session:
        # Create an approved portal with specific allowed_paths
        test_domain = f"governance-{uuid.uuid4().hex[:8]}.gov.in"
        portal = ApprovedPortal(
            id=uuid.uuid4(),
            portal_name="National Governance Portal",
            domain=test_domain,
            entry_url=f"https://{test_domain}/services",
            category="Governance & Welfare",
            is_active=True,
            allowed_paths=["/services", "/apply"],
            support_tier="tier_1_verified",
            source="official_gazette"
        )
        session.add(portal)
        await session.commit()

        # 1. Valid domain and permitted path
        valid_url = f"https://{test_domain}/services/pension"
        check_ok = await check_domain_shield(valid_url, session, validate_redirects=False)
        assert check_ok.is_allowed is True
        assert check_ok.shield_status == "VERIFIED_OFFICIAL"
        assert check_ok.allowed_paths == ["/services", "/apply"]

        # 2. Valid domain but prohibited/unallowed path (FR-012)
        invalid_path_url = f"https://{test_domain}/admin/unauthorized"
        check_path_blocked = await check_domain_shield(invalid_path_url, session, validate_redirects=False)
        assert check_path_blocked.is_allowed is False
        assert check_path_blocked.shield_status == "BLOCKED_UNOFFICIAL"
        assert "not within permitted allowed_paths" in (check_path_blocked.warning or "")

        # 3. HTTP Redirect Chain Validation (FR-013)
        # Mock httpx client where initial request redirects to an unapproved malicious domain
        redirect_target = "https://malicious-phishing-site.org/fake-login"

        def redirect_handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == test_domain:
                return httpx.Response(302, headers={"Location": redirect_target})
            return httpx.Response(200, text="Phishing payload")

        mock_transport = httpx.MockTransport(redirect_handler)
        async with httpx.AsyncClient(transport=mock_transport) as redirect_client:
            redirect_check = await check_domain_shield(
                f"https://{test_domain}/services/form",
                session,
                validate_redirects=True,
                client=redirect_client,
            )
            assert redirect_check.is_allowed is False
            assert redirect_check.shield_status == "BLOCKED_UNOFFICIAL"
            assert any("malicious-phishing-site.org" in hop for hop in redirect_check.redirect_chain)
            assert "Redirect chain escaped approved domain" in (redirect_check.warning or "")

    print("✓ Domain Shield allowed_paths and HTTP redirect chain validation passed.")


@pytest.mark.anyio
async def test_synthetic_annotated_visual_guide():
    """Verify synthetic annotated visual guide rendering with Pillow base64 PNG (FR-051)."""
    # Test all document templates
    topics = ["passbook", "pan", "aadhaar", "income"]
    for topic in topics:
        card = get_visual_guide_card(topic, language="en")
        assert card is not None
        assert card.annotated_image_url is not None
        assert card.annotated_image_url.startswith("data:image/png;base64,")

        # Decode base64 image data
        header, b64_data = card.annotated_image_url.split(",", 1)
        raw_bytes = base64.b64decode(b64_data)
        assert len(raw_bytes) > 1000

        # Verify image format and resolution
        img = Image.open(io.BytesIO(raw_bytes))
        assert img.format == "PNG"
        assert img.size == (600, 360)

    print("✓ Synthetic annotated visual guide PNG generation passed.")
