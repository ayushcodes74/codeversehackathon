"""Google OAuth authentication and Landing Page AI Problem-to-Portal Recommender routes."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.db_routes import create_hs256_jwt
from app.core.config import get_settings
from app.database import get_db
from app.llm.client import UnconfiguredLLMClient, get_llm_client
from app.models import ApprovedPortal, FamilyProfile, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Google Auth & Portal Discovery"])


class GoogleAuthRequest(BaseModel):
    access_token: str | None = None
    id_token: str | None = None
    preferred_language: str = "en"
    dev_email: str | None = None
    dev_name: str | None = None


class RecommendPortalRequest(BaseModel):
    problem: str = Field(..., min_length=2, max_length=1000)
    language: str = "en"


class RecommendPortalResponse(BaseModel):
    portal_name: str
    portal_url: str
    domain: str
    category: str
    reason: str
    required_documents: list[str]
    steps: list[str]
    is_local_demo: bool = False


_DEFAULT_PORTAL_CATALOG: list[dict[str, Any]] = [
    {
        "portal_name": "National Scholarship Portal (NSP)",
        "domain": "scholarships.gov.in",
        "entry_url": "https://scholarships.gov.in/",
        "category": "Education / Scholarships",
        "keywords": ["scholarship", "nsp", "post-matric", "post matric", "pre-matric", "fellowship", "chhatravritti", "छात्रवृत्ति", "college", "student"],
        "required_documents": ["Aadhaar Card", "Bank Passbook (IFSC)", "Previous Marksheet", "Income Certificate"],
        "reason_en": "National Scholarship Portal is the official Government of India portal for Central and State Post-Matric / Pre-Matric scholarships.",
        "reason_hi": "National Scholarship Portal (NSP) भारत सरकार का आधिकारिक पोर्टल है जहाँ आप Post-Matric / Pre-Matric छात्रवृत्ति के लिए आवेदन कर सकते हैं।",
    },
    {
        "portal_name": "PAN Card Services (Protean / NSDL)",
        "domain": "onlineservices.nsdl.com",
        "entry_url": "https://www.onlineservices.nsdl.com/paam/endUserRegisterContact.html",
        "category": "Tax & Identity",
        "keywords": ["pan", "pan card", "nsdl", "tax", "पैन"],
        "required_documents": ["Aadhaar Card", "Passport-size Photo", "Signature"],
        "reason_en": "Official Protean (NSDL) e-Gov portal for new PAN card application and corrections.",
        "reason_hi": "नए PAN कार्ड आवेदन और सुधार के लिए यह आधिकारिक Protean (NSDL) पोर्टल है।",
    },
    {
        "portal_name": "Passport Seva Portal (MEA)",
        "domain": "passportindia.gov.in",
        "entry_url": "https://www.passportindia.gov.in/",
        "category": "Travel & Identity",
        "keywords": ["passport", "visa", "mea", "पासपोर्ट"],
        "required_documents": ["Aadhaar Card", "PAN Card", "10th Marksheet (DOB Proof)"],
        "reason_en": "Official Ministry of External Affairs Passport Seva portal for fresh passport and renewal applications.",
        "reason_hi": "नए पासपोर्ट और नवीनीकरण के लिए यह विदेश मंत्रालय का आधिकारिक Passport Seva पोर्टल है।",
    },
    {
        "portal_name": "e-District / State Citizen Services (Income Certificate)",
        "domain": "edistrict.up.gov.in",
        "entry_url": "https://edistrict.up.gov.in/",
        "category": "State Certificates",
        "keywords": ["income certificate", "caste certificate", "domicile", "aay", "jati", "niwas", "आय प्रमाण", "e-district", "edistrict"],
        "required_documents": ["Aadhaar Card", "Self-Declaration Form", "Salary / Income Proof"],
        "reason_en": "Official e-District portal for Income, Caste, and Domicile certificates.",
        "reason_hi": "आय, जाति और निवास प्रमाण पत्र (Income / Caste / Domicile) के लिए आधिकारिक e-District पोर्टल।",
    },
    {
        "portal_name": "Parivahan Sewa (Driving License & RC)",
        "domain": "parivahan.gov.in",
        "entry_url": "https://parivahan.gov.in/",
        "category": "Transport & Licensing",
        "keywords": ["driving", "license", "dl", "ll", "learner", "rc", "vehicle", "parivahan", "ड्राइविंग"],
        "required_documents": ["Aadhaar Card", "Age Proof (Marksheet)", "Address Proof"],
        "reason_en": "Official Ministry of Road Transport Parivahan portal for Learner's and Driving Licenses.",
        "reason_hi": "ड्राइविंग लाइसेंस (LL / DL) और वाहन सेवाओं के लिए आधिकारिक Parivahan पोर्टल।",
    },
    {
        "portal_name": "Hackathon Local Synthetic Demo Portal",
        "domain": "127.0.0.1:5500",
        "entry_url": "http://127.0.0.1:5500/demo-form.html",
        "category": "Development & Testing",
        "keywords": ["demo", "test", "synthetic", "hackathon", "local", "sample"],
        "required_documents": ["Aadhaar Card", "PAN Card", "Bank Passbook"],
        "reason_en": "Local synthetic scholarship & citizen form portal for testing Zero-Leak CryptoVault autofill.",
        "reason_hi": "Zero-Leak CryptoVault ऑटो-फिल टेस्ट करने के लिए स्थानीय सिंथेटिक डेमो पोर्टल।",
        "is_local_demo": True,
    },
]


@router.post("/auth/google")
async def google_oauth_login(
    payload: GoogleAuthRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify Google OAuth access_token/id_token (or dev demo fallback), upsert User, and issue HS256 JWT."""
    settings = get_settings()
    configured_client_id = (settings.google_client_id or "").strip()
    is_placeholder_client = (
        not configured_client_id
        or configured_client_id.startswith("YOUR_GOOGLE_CLIENT_ID")
    )

    google_sub: str | None = None
    email: str | None = None
    full_name: str | None = None
    picture: str | None = None
    auth_mode = "google_oauth"

    if payload.id_token:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": payload.id_token},
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired Google id_token.",
            )
        info = resp.json()
        if not is_placeholder_client and info.get("aud") != configured_client_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google token audience does not match GOOGLE_CLIENT_ID.",
            )
        google_sub = info.get("sub")
        email = info.get("email")
        full_name = info.get("name") or (email.split("@")[0] if email else "Citizen User")
        picture = info.get("picture")

    elif payload.access_token and not payload.access_token.startswith("dev_google_token_"):
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {payload.access_token}"},
            )
        if resp.status_code == 200:
            info = resp.json()
            google_sub = info.get("sub")
            email = info.get("email")
            full_name = info.get("name") or (email.split("@")[0] if email else "Citizen User")
            picture = info.get("picture")
        elif is_placeholder_client and not settings.is_production:
            auth_mode = "dev_demo_google"
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired Google access_token.",
            )
    else:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Google OAuth token is required in production.",
            )
        auth_mode = "dev_demo_google"

    if auth_mode == "dev_demo_google":
        email = (payload.dev_email or "citizen.demo@gmail.com").strip().lower()
        full_name = (payload.dev_name or "Citizen User").strip()
        google_sub = f"google_demo_{email}"
        picture = None

    if not google_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not resolve Google account identity (sub/email).",
        )

    stmt = select(User).where(User.google_sub == google_sub)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        email_res = await db.execute(select(User).where(User.email == email))
        user = email_res.scalar_one_or_none()

    if not user:
        user = User(
            google_sub=google_sub,
            email=email,
            full_name=full_name or "Citizen User",
            preferred_language=payload.preferred_language or "en",
        )
        db.add(user)
        await db.flush()

        default_profile = FamilyProfile(
            user_id=user.id,
            profile_slug="self",
            display_name=user.full_name,
            relation_type="self",
            is_default=True,
        )
        db.add(default_profile)
        await db.flush()
    else:
        user.google_sub = google_sub
        if full_name:
            user.full_name = full_name
        user.preferred_language = payload.preferred_language or user.preferred_language
        await db.flush()

    prof_res = await db.execute(
        select(FamilyProfile).where(FamilyProfile.user_id == user.id)
    )
    profiles = prof_res.scalars().all()
    default_prof = next((p for p in profiles if p.is_default), profiles[0] if profiles else None)

    jwt_token = create_hs256_jwt(str(user.id), expires_in_seconds=86400)
    return {
        "access_token": jwt_token,
        "token_type": "bearer",
        "user_id": str(user.id),
        "profile_id": str(default_prof.id) if default_prof else None,
        "email": user.email,
        "name": user.full_name,
        "picture": picture,
        "auth_mode": auth_mode,
    }


@router.post("/discovery/recommend-portal", response_model=RecommendPortalResponse)
async def recommend_portal_for_problem(
    payload: RecommendPortalRequest,
    db: AsyncSession = Depends(get_db),
):
    """Analyze user's natural-language problem (EN/Hinglish) and return the matched official portal."""
    problem = payload.problem.strip()
    clean = problem.lower()
    lang = "hi" if payload.language.lower().startswith("hi") else "en"

    # Load active portals from PostgreSQL
    db_portals = (
        await db.execute(select(ApprovedPortal).where(ApprovedPortal.is_active == True))  # noqa: E712
    ).scalars().all()

    candidates: list[dict[str, Any]] = []
    seen_domains: set[str] = set()
    for p in db_portals:
        seen_domains.add(p.domain.lower())
        candidates.append({
            "portal_name": p.portal_name,
            "domain": p.domain,
            "entry_url": p.entry_url,
            "category": p.category,
            "keywords": [p.portal_name.lower(), p.category.lower(), p.domain.lower()],
            "required_documents": p.required_doc_types or ["Aadhaar Card", "PAN Card"],
            "reason_en": f"{p.portal_name} ({p.domain}) is verified in the Official Domain Shield registry for {p.category}.",
            "reason_hi": f"{p.portal_name} ({p.domain}) Official Domain Shield में {p.category} के लिए सत्यापित पोर्टल है।",
            "is_local_demo": p.domain.startswith("127.0.0.1") or p.domain.startswith("localhost"),
        })

    for item in _DEFAULT_PORTAL_CATALOG:
        if item["domain"].lower() not in seen_domains:
            candidates.append(item)

    # Try LLM recommendation first if configured
    llm = get_llm_client()
    if not isinstance(llm, UnconfiguredLLMClient) and hasattr(llm, "generate_json"):
        try:
            catalog_summary = [
                {"domain": c["domain"], "portal_name": c["portal_name"], "category": c["category"], "entry_url": c["entry_url"]}
                for c in candidates
            ]
            system_prompt = (
                "You are an Indian Citizen Service Portal Router. Given the user's goal in English or Hinglish, "
                "select the single best official portal from the provided catalog JSON. "
                "Respond ONLY with valid JSON: {\"domain\": \"...\", \"reason\": \"...\", \"required_documents\": [\"...\"]}."
            )
            user_prompt = f"User goal ({lang}): {problem}\n\nCatalog:\n{json.dumps(catalog_summary)}"
            resp = await llm.generate_json(system_prompt=system_prompt, user_prompt=user_prompt)
            if isinstance(resp, dict) and resp.get("domain"):
                chosen_domain = str(resp["domain"]).lower().strip()
                match = next((c for c in candidates if c["domain"].lower() == chosen_domain), None)
                if match:
                    return RecommendPortalResponse(
                        portal_name=match["portal_name"],
                        portal_url=match["entry_url"],
                        domain=match["domain"],
                        category=match["category"],
                        reason=str(resp.get("reason") or (match["reason_hi"] if lang == "hi" else match["reason_en"])),
                        required_documents=resp.get("required_documents") or match["required_documents"],
                        steps=[
                            "1. Open the official portal via the button below.",
                            "2. Complete manual login / OTP / CAPTCHA on the portal yourself.",
                            "3. Open the Universal AI Assistant Chrome Extension to upload docs, ask doubts, and auto-fill safely.",
                        ],
                        is_local_demo=bool(match.get("is_local_demo", False)),
                    )
        except Exception as exc:
            logger.debug("LLM portal recommendation fallback to keyword heuristics: %s", exc)

    # Keyword heuristic matching (EN + Hinglish)
    best_match = candidates[0]
    best_score = -1
    for c in candidates:
        score = 0
        for kw in c.get("keywords", []):
            if kw in clean:
                score += 2
        for word in clean.split():
            if len(word) >= 3 and (word in c["portal_name"].lower() or word in c["category"].lower()):
                score += 1
        if score > best_score:
            best_score = score
            best_match = c

    # If zero keywords matched, pick NSP (scholarship) or first official portal
    if best_score <= 0:
        best_match = next(
            (c for c in candidates if "scholarship" in c["portal_name"].lower()),
            candidates[0],
        )

    return RecommendPortalResponse(
        portal_name=best_match["portal_name"],
        portal_url=best_match["entry_url"],
        domain=best_match["domain"],
        category=best_match["category"],
        reason=best_match["reason_hi"] if lang == "hi" else best_match["reason_en"],
        required_documents=best_match["required_documents"],
        steps=[
            "1. Open the official portal via the button below.",
            "2. Complete manual login / OTP / CAPTCHA on the portal yourself.",
            "3. Open the Universal AI Assistant Chrome Extension to upload docs, ask doubts, and auto-fill safely.",
        ],
        is_local_demo=bool(best_match.get("is_local_demo", False)),
    )
