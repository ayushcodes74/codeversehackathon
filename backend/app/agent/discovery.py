"""Goal-to-Portal Discovery & Official Domain Shield.

Enforces:
1. Official Domain Shield: Validates URLs against the approved_portals table in PostgreSQL.
   Blocks unverified or lookalike domains; allows official portals and local development/test hosts.
2. Slot-Filling & Disambiguation: Checks AgentMemory facts (domicile_state, education_level)
   before recommending portals; prompts user clarifying questions instead of dumping multiple links.
"""

from __future__ import annotations
import uuid
from urllib.parse import urlparse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import ApprovedPortal, AgentMemory
from app.schemas.agent_contracts import (
    VerifyDomainOutput,
    ApprovedPortalItem,
    RequestUserInputInput,
)

# Trusted local development hosts for testing synthetic portals (HTTP/HTTPS only)
LOCAL_DEV_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


def normalize_host(url: str) -> str:
    """Extract and normalize hostname from URL."""
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"http://{url}")
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    return host.removeprefix("www.")


async def check_domain_shield(url: str, db: AsyncSession) -> VerifyDomainOutput:
    """Validates the target URL against the PostgreSQL approved_portals table.

    Blocks file:// and non-HTTP(S) schemes, punycode/IDN homographs, and unregistered domains.
    """
    raw = (url or "").strip()
    if not raw:
        return VerifyDomainOutput(
            is_allowed=False,
            domain="",
            shield_status="BLOCKED_UNOFFICIAL",
            warning="No URL provided for domain verification.",
        )

    if "://" in raw:
        scheme = urlparse(raw).scheme.lower()
        if scheme not in ("http", "https"):
            return VerifyDomainOutput(
                is_allowed=False,
                domain="",
                shield_status="BLOCKED_UNOFFICIAL",
                warning=f"URL scheme '{scheme}://' is blocked by Official Domain Shield.",
            )

    host = normalize_host(raw)
    if not host:
        return VerifyDomainOutput(
            is_allowed=False,
            domain="",
            shield_status="BLOCKED_UNOFFICIAL",
            warning="Invalid or unparseable URL.",
        )

    # Block punycode / non-ASCII IDN homograph domains.
    labels = host.split(".")
    if any(lbl.startswith("xn--") for lbl in labels) or not host.isascii():
        return VerifyDomainOutput(
            is_allowed=False,
            domain=host,
            shield_status="BLOCKED_UNOFFICIAL",
            warning=f"Punycode or non-ASCII homograph domain '{host}' is blocked by Official Domain Shield.",
        )

    # 1. Allow local dev synthetic demo portal over HTTP/HTTPS only
    if host in LOCAL_DEV_HOSTS:
        return VerifyDomainOutput(
            is_allowed=True,
            domain=host,
            portal_name="Synthetic Demo Portal",
            category="Development & Testing",
            shield_status="LOCAL_DEV_ALLOWED",
            warning=None,
        )

    # 2. Query approved_portals in PostgreSQL
    stmt = select(ApprovedPortal).where(ApprovedPortal.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    portals = result.scalars().all()

    for portal in portals:
        approved_domain = portal.domain.lower().strip().removeprefix("www.")
        if host == approved_domain:
            return VerifyDomainOutput(
                is_allowed=True,
                domain=host,
                portal_name=portal.portal_name,
                category=portal.category,
                shield_status="VERIFIED_OFFICIAL",
                warning=None,
            )

    # 3. Domain not on whitelist -> Block!
    return VerifyDomainOutput(
        is_allowed=False,
        domain=host,
        shield_status="BLOCKED_UNOFFICIAL",
        warning=f"Domain '{host}' is NOT on the verified official government portal whitelist. Auto-fill blocked by Domain Shield.",
    )


async def discover_or_narrow_portal(
    user_id: uuid.UUID,
    profile_id: uuid.UUID | None,
    goal_text: str,
    language: str,
    db: AsyncSession,
) -> tuple[ApprovedPortalItem | None, RequestUserInputInput | None, str]:
    """Resolves a user goal to a single verified portal URL.
    
    If the goal is broad (e.g. "I want a scholarship") and multiple candidates exist,
    checks AgentMemory for missing criteria (domicile_state, education_level).
    If missing, returns a clarifying RequestUserInputInput question instead of dumping links.
    """
    clean_goal = goal_text.lower().strip()

    # Fetch user memory facts for state and education
    memories: dict[str, str] = {}
    stmt_mem = select(AgentMemory).where(AgentMemory.user_id == user_id)
    if profile_id:
        stmt_mem = stmt_mem.where(AgentMemory.profile_id == profile_id)
    mem_result = await db.execute(stmt_mem)
    for mem in mem_result.scalars().all():
        memories[mem.fact_key.lower()] = mem.fact_value

    # Fetch all active portals
    stmt_portals = select(ApprovedPortal).where(ApprovedPortal.is_active == True)  # noqa: E712
    portal_result = await db.execute(stmt_portals)
    all_portals = portal_result.scalars().all()

    # Determine goal category keywords
    is_scholarship = any(w in clean_goal for w in ["scholarship", "nsp", "fellowship", "छात्रवृत्ति", "welfare"])
    is_pan = any(w in clean_goal for w in ["pan", "pan card", "nsdl", "uti", "पैन"])
    is_demo = any(w in clean_goal for w in ["demo", "test", "synthetic", "sample", "डेमो"])

    matched: list[ApprovedPortal] = []
    for p in all_portals:
        p_cat = p.category.lower()
        p_name = p.portal_name.lower()
        if is_demo and ("demo" in p_cat or "test" in p_name):
            matched.append(p)
        elif is_scholarship and ("scholarship" in p_cat or "education" in p_cat or "scholarship" in p_name):
            matched.append(p)
        elif is_pan and ("pan" in p_cat or "identity" in p_cat or "pan" in p_name):
            matched.append(p)
        elif clean_goal in p_name or clean_goal in p_cat:
            matched.append(p)

    # Fallback to all portals if none matched specifically
    candidates = matched if matched else all_portals

    # If demo is explicitly requested or running locally, prioritize demo if present
    if is_demo:
        for c in candidates:
            if "demo" in c.portal_name.lower() or "synthetic" in c.portal_name.lower():
                item = ApprovedPortalItem(
                    id=c.id,
                    portal_name=c.portal_name,
                    domain=c.domain,
                    entry_url=c.entry_url,
                    category=c.category,
                    domicile_state=c.domicile_state,
                    education_level=c.education_level,
                    required_doc_types=c.required_doc_types or []
                )
                msg = f"Navigating to official portal: {c.portal_name}"
                return item, None, msg

    # Narrowing logic for Scholarships
    if is_scholarship and len(candidates) > 1:
        # Check domicile state
        domicile = memories.get("domicile_state") or memories.get("state")
        if not domicile:
            # Need to ask user for domicile_state
            q_en = "Which state are you a permanent resident of? (State-specific scholarships offer dedicated quotas)"
            q_hi = "आप किस राज्य के स्थायी निवासी हैं? (राज्य छात्रवृत्तियों में अलग कोटा होता है)"
            question = RequestUserInputInput(
                missing_field_key="domicile_state",
                question_en=q_en,
                question_hi=q_hi,
                input_type="select",
                options=["Karnataka", "Uttar Pradesh", "Maharashtra", "Delhi", "All India / Central"]
            )
            msg = q_hi if language == "hi" else q_en
            return None, question, msg

        # Check education level if still ambiguous
        edu_level = memories.get("education_level") or memories.get("education")
        if not edu_level:
            q_en = "What is your current education level? (e.g., Pre-Matric, Post-Matric, Higher Education)"
            q_hi = "आपकी वर्तमान शिक्षा का स्तर क्या है? (जैसे: प्री-मैट्रिक, पोस्ट-मैट्रिक, उच्च शिक्षा)"
            question = RequestUserInputInput(
                missing_field_key="education_level",
                question_en=q_en,
                question_hi=q_hi,
                input_type="select",
                options=["Pre-Matric (Class 1-10)", "Post-Matric (Class 11-12 / Diploma / Degree)", "Higher Education / Research"]
            )
            msg = q_hi if language == "hi" else q_en
            return None, question, msg

        # Filter candidates by state
        state_candidates = [c for c in candidates if c.domicile_state and c.domicile_state.lower() == domicile.lower()]
        if state_candidates:
            candidates = state_candidates

    # If we have at least one candidate, pick best match
    if candidates:
        chosen = candidates[0]
        item = ApprovedPortalItem(
            id=chosen.id,
            portal_name=chosen.portal_name,
            domain=chosen.domain,
            entry_url=chosen.entry_url,
            category=chosen.category,
            domicile_state=chosen.domicile_state,
            education_level=chosen.education_level,
            required_doc_types=chosen.required_doc_types or []
        )
        msg_en = f"Found verified government portal: {chosen.portal_name} ({chosen.domain}). Ready to begin application."
        msg_hi = f"सत्यापित सरकारी पोर्टल मिला: {chosen.portal_name} ({chosen.domain})। आवेदन शुरू करने के लिए तैयार।"
        return item, None, msg_hi if language == "hi" else msg_en

    # If no candidate found
    return None, None, "No approved government portal found for this request."
