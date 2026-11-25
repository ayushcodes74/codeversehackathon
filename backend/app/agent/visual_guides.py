"""Contextual Q&A and Visual Reference Guide Cards.

Provides instant mid-form visual guidance (e.g. "where is my account number",
"where is my PAN", "where is IFSC", "passbook copy") without resetting or losing
the active form automation step state.
"""

from __future__ import annotations
from typing import Optional
from app.schemas.agent_contracts import VisualGuideCard, CalloutBox


# Knowledge base of visual guides with bilingual explanations and bounding-box coordinates
VISUAL_GUIDE_REGISTRY: list[VisualGuideCard] = [
    VisualGuideCard(
        card_id="guide_bank_account_passbook",
        topic="bank_account",
        title="Bank Account Number on Passbook",
        title_hi="पासबुक पर बैंक खाता संख्या",
        explanation="Your Bank Account Number is typically printed on the first page of your physical bank passbook, right below your name and customer ID. It is usually 9 to 18 digits long.",
        explanation_hi="आपकी बैंक खाता संख्या आमतौर पर पासबुक के पहले पृष्ठ पर आपके नाम और ग्राहक आईडी (Customer ID) के नीचे मुद्रित होती है। यह सामान्यतः 9 से 18 अंकों की होती है।",
        document_sample_id="sample_bank_passbook",
        bounding_box_callouts=[
            CalloutBox(
                label="Account Number",
                label_hi="खाता संख्या",
                description="9 to 18 digit unique bank account number",
                description_hi="9 से 18 अंकों की बैंक खाता संख्या",
                x=18.0,
                y=38.0,
                width=34.0,
                height=9.0
            ),
            CalloutBox(
                label="Customer ID / CIF",
                label_hi="ग्राहक आईडी / CIF",
                description="Internal bank reference number (do not confuse with account number)",
                description_hi="बैंक का आंतरिक ग्राहक नंबर",
                x=18.0,
                y=28.0,
                width=28.0,
                height=7.0
            )
        ],
        quick_action="FOCUS_INPUT"
    ),
    VisualGuideCard(
        card_id="guide_bank_ifsc",
        topic="ifsc_code",
        title="IFSC Code on Cheque or Passbook",
        title_hi="पासबुक या चेक पर IFSC कोड",
        explanation="The IFSC (Indian Financial System Code) is an 11-character alphanumeric code used for NEFT/RTGS/IMPS transfers. The first 4 characters represent the bank name, the 5th character is always '0', and the last 6 characters represent the specific branch.",
        explanation_hi="IFSC कोड 11 अक्षरों और अंकों का कोड होता है। पहले 4 अक्षर बैंक का नाम दर्शाते हैं, 5वाँ अक्षर हमेशा '0' होता है, और अंतिम 6 अंक शाखा (Branch) की पहचान करते हैं।",
        document_sample_id="sample_bank_passbook",
        bounding_box_callouts=[
            CalloutBox(
                label="IFSC Code (11 Chars)",
                label_hi="IFSC कोड (11 अक्षर)",
                description="Branch identifier, e.g. SBIN0001234 or HDFC0000456",
                description_hi="शाखा कोड, जैसे SBIN0001234",
                x=18.0,
                y=52.0,
                width=30.0,
                height=9.0
            )
        ],
        quick_action="FOCUS_INPUT"
    ),
    VisualGuideCard(
        card_id="guide_pan_card",
        topic="pan_card",
        title="Permanent Account Number (PAN)",
        title_hi="पैन कार्ड (स्थायी खाता संख्या)",
        explanation="Your 10-character PAN is printed prominently in the center of your PAN card below your father's name and above your date of birth. Format: 5 letters, 4 numbers, 1 letter (e.g. ABCDE1234F).",
        explanation_hi="आपका 10-अक्षरों का पैन नंबर पैन कार्ड के बीच में पिता के नाम के नीचे और जन्मतिथि के ऊपर लिखा होता है। प्रारूप: 5 अक्षर, 4 अंक, 1 अक्षर (उदा: ABCDE1234F)।",
        document_sample_id="sample_pan_card",
        bounding_box_callouts=[
            CalloutBox(
                label="Permanent Account Number",
                label_hi="पैन नंबर",
                description="10-character alphanumeric PAN number",
                description_hi="10 अक्षरों और अंकों का पैन नंबर",
                x=15.0,
                y=58.0,
                width=42.0,
                height=11.0
            ),
            CalloutBox(
                label="Date of Birth (DOB)",
                label_hi="जन्मतिथि (DOB)",
                description="Date of birth in DD/MM/YYYY format",
                description_hi="DD/MM/YYYY प्रारूप में जन्मतिथि",
                x=15.0,
                y=73.0,
                width=25.0,
                height=8.0
            )
        ],
        quick_action="TRIGGER_FILE_PICKER"
    ),
    VisualGuideCard(
        card_id="guide_aadhaar_card",
        topic="aadhaar_card",
        title="12-Digit Aadhaar Number",
        title_hi="12-अंकीय आधार संख्या",
        explanation="Your Aadhaar number is the 12-digit number divided into three 4-digit groups (e.g. 1234 5678 9012) printed across the bottom-center of your Aadhaar card.",
        explanation_hi="आपकी आधार संख्या 12 अंकों की संख्या है जो तीन 4-अंकीय समूहों (उदा: 1234 5678 9012) में कार्ड के निचले हिस्से में मुद्रित होती है।",
        document_sample_id="sample_aadhaar",
        bounding_box_callouts=[
            CalloutBox(
                label="12-Digit Aadhaar Number",
                label_hi="12-अंकीय आधार संख्या",
                description="Government unique identity number",
                description_hi="विशिष्ट पहचान संख्या",
                x=22.0,
                y=76.0,
                width=56.0,
                height=12.0
            )
        ],
        quick_action="TRIGGER_FILE_PICKER"
    ),
    VisualGuideCard(
        card_id="guide_income_certificate",
        topic="income_certificate",
        title="Income Certificate Number / RD Number",
        title_hi="आय प्रमाण पत्र संख्या (RD Number)",
        explanation="The Certificate Serial Number or RD (Revenue Department) Number is printed in the top right or top left header of your state Revenue Department Income Certificate.",
        explanation_hi="प्रमाण पत्र क्रमांक या आरडी (RD) नंबर आपके राज्य के राजस्व विभाग के आय प्रमाण पत्र के सबसे ऊपर दाएं या बाएं कोने में लिखा होता है।",
        document_sample_id="sample_income_cert",
        bounding_box_callouts=[
            CalloutBox(
                label="Application / Certificate No.",
                label_hi="आवेदन / प्रमाण पत्र संख्या",
                description="Unique RD serial number, e.g. RD0038291048",
                description_hi="अद्वितीय आरडी नंबर",
                x=62.0,
                y=18.0,
                width=32.0,
                height=8.0
            )
        ],
        quick_action="TRIGGER_FILE_PICKER"
    ),
]


def is_visual_guide_query(query: str) -> bool:
    """Checks whether the user's message is a contextual visual guide or knowledge question."""
    if not query:
        return False
    q = query.lower()
    trigger_words = [
        "where is", "where can i find", "how to find", "how do i find",
        "show me", "what is", "where is my", "which is",
        "कहाँ है", "कहाँ मिलेगा", "दिखाओ", "क्या है", "कैसे खोजें",
        "passbook", "account number", "ifsc", "pan", "aadhaar", "cheque",
        "खाता संख्या", "अकाउंट", "आईएफएससी", "पैन कार्ड", "आधार"
    ]
    return any(w in q for w in trigger_words)


def get_visual_guide_card(
    query: str,
    language: str = "en",
    target_element_key: Optional[str] = None
) -> Optional[VisualGuideCard]:
    """Finds the best matching VisualGuideCard for a query."""
    q = query.lower()

    # Bank Account / Passbook
    if any(w in q for w in ["account number", "account no", "passbook", "खाता संख्या", "अकाउंट"]):
        card = next((c for c in VISUAL_GUIDE_REGISTRY if c.card_id == "guide_bank_account_passbook"), None)
    # IFSC Code
    elif any(w in q for w in ["ifsc", "ifsc code", "आईएफएससी", "branch code"]):
        card = next((c for c in VISUAL_GUIDE_REGISTRY if c.card_id == "guide_bank_ifsc"), None)
    # PAN Card
    elif any(w in q for w in ["pan", "pan card", "permanent account number", "पैन"]):
        card = next((c for c in VISUAL_GUIDE_REGISTRY if c.card_id == "guide_pan_card"), None)
    # Aadhaar Card
    elif any(w in q for w in ["aadhaar", "aadhar", "uidai", "आधार"]):
        card = next((c for c in VISUAL_GUIDE_REGISTRY if c.card_id == "guide_aadhaar_card"), None)
    # Income Certificate
    elif any(w in q for w in ["income", "income cert", "rd number", "आय"]):
        card = next((c for c in VISUAL_GUIDE_REGISTRY if c.card_id == "guide_income_certificate"), None)
    else:
        card = None

    if card:
        # Clone card so we can associate target_element_key if provided
        card_copy = card.model_copy(deep=True)
        if target_element_key:
            card_copy.target_element_key = target_element_key
        return card_copy

    return None
