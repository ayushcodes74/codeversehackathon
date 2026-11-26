"""Contextual Q&A and Visual Reference Guide Cards.

Provides instant mid-form visual guidance (e.g. "where is my account number",
"where is my PAN", "where is IFSC", "passbook copy") without resetting or losing
the active form automation step state.
"""

from __future__ import annotations
import base64
import io
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

from app.schemas.agent_contracts import VisualGuideCard, CalloutBox


def render_synthetic_annotated_image(card: VisualGuideCard) -> str:
    """Renders a synthetic annotated guide image with bounding boxes, step badges, and callouts (FR-051).

    Generates a 600x360 PNG canvas representing the document template, overlaying
    precise bounding boxes and indexed callout labels, encoded as a base64 data URL.
    """
    width, height = 600, 360
    img = Image.new("RGBA", (width, height), color=(255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    sample_id = card.document_sample_id or ""

    # 1. Render Document Canvas & Template Textures
    if "passbook" in sample_id:
        # Bank Passbook: Light cream background with header banner
        draw.rectangle([(10, 10), (width - 10, height - 10)], fill=(253, 251, 247), outline=(203, 213, 225), width=2)
        draw.rectangle([(10, 10), (width - 10, 55)], fill=(37, 99, 235))
        draw.text((25, 22), "NATIONAL BANK OF INDIA - SAVINGS ACCOUNT PASSBOOK", fill=(255, 255, 255), font=font)
        
        # Details text
        draw.text((30, 75), "Branch: New Delhi Main Branch  |  Code: 00123", fill=(71, 85, 105), font=font)
        draw.text((30, 95), "Account Holder: AYUSH KUMAR", fill=(15, 23, 42), font=font)
        draw.text((30, 115), "Customer ID / CIF: 890123456", fill=(30, 41, 59), font=font)
        draw.text((30, 145), "Account Number: 12345678901234", fill=(15, 23, 42), font=font)
        draw.text((30, 195), "IFSC Code: SBIN0001234   |   MICR: 110002001", fill=(15, 23, 42), font=font)
        draw.text((30, 240), "Address: Flat 402, Block B, Green Park, New Delhi - 110016", fill=(71, 85, 105), font=font)

    elif "pan" in sample_id:
        # PAN Card: Sky blue card with emblem header
        draw.rectangle([(10, 10), (width - 10, height - 10)], fill=(238, 242, 255), outline=(199, 210, 254), width=2)
        draw.rectangle([(10, 10), (width - 10, 55)], fill=(30, 58, 138))
        draw.text((25, 22), "INCOME TAX DEPARTMENT - GOVT. OF INDIA", fill=(255, 255, 255), font=font)

        draw.text((30, 75), "Permanent Account Number Card", fill=(79, 70, 229), font=font)
        draw.text((30, 110), "Name: AYUSH KUMAR", fill=(15, 23, 42), font=font)
        draw.text((30, 140), "Father's Name: RAJESH KUMAR", fill=(15, 23, 42), font=font)
        draw.text((30, 180), "PAN: ABCDE1234F", fill=(15, 23, 42), font=font)
        draw.text((30, 250), "Date of Birth: 15/08/1998", fill=(15, 23, 42), font=font)

        # Photo placeholder
        draw.rectangle([(440, 75), (550, 200)], fill=(226, 232, 240), outline=(148, 163, 184), width=2)
        draw.text((465, 130), "[ PHOTO ]", fill=(100, 116, 139), font=font)

    elif "aadhaar" in sample_id:
        # Aadhaar: White card with tricolor accent
        draw.rectangle([(10, 10), (width - 10, height - 10)], fill=(255, 255, 255), outline=(203, 213, 225), width=2)
        draw.rectangle([(10, 10), (width - 10, 22)], fill=(249, 115, 22))
        draw.rectangle([(10, height - 22), (width - 10, height - 10)], fill=(34, 197, 94))
        draw.text((25, 35), "GOVERNMENT OF INDIA - UNIQUE IDENTIFICATION AUTHORITY", fill=(15, 23, 42), font=font)

        draw.text((30, 80), "To: Ayush Kumar", fill=(15, 23, 42), font=font)
        draw.text((30, 105), "S/O: Rajesh Kumar", fill=(71, 85, 105), font=font)
        draw.text((30, 130), "DOB: 15/08/1998  |  Gender: MALE", fill=(71, 85, 105), font=font)
        
        # Aadhaar Number in center
        draw.rectangle([(120, 240), (480, 290)], fill=(241, 245, 249), outline=(148, 163, 184), width=1)
        draw.text((180, 258), "1234   5678   9012", fill=(15, 23, 42), font=font)
        draw.text((195, 305), "Mera Aadhaar, Meri Pehchan", fill=(100, 116, 139), font=font)

        # Photo placeholder
        draw.rectangle([(440, 60), (550, 185)], fill=(226, 232, 240), outline=(148, 163, 184), width=2)
        draw.text((465, 115), "[ PHOTO ]", fill=(100, 116, 139), font=font)

    else:
        # Default / Income Certificate
        draw.rectangle([(10, 10), (width - 10, height - 10)], fill=(255, 251, 235), outline=(252, 211, 77), width=2)
        draw.rectangle([(10, 10), (width - 10, 50)], fill=(180, 83, 9))
        draw.text((25, 20), "REVENUE DEPARTMENT - INCOME CERTIFICATE", fill=(255, 255, 255), font=font)
        draw.text((30, 75), "Application Ref / RD No: RD0038291048", fill=(15, 23, 42), font=font)
        draw.text((30, 115), "Applicant Name: AYUSH KUMAR", fill=(15, 23, 42), font=font)
        draw.text((30, 155), "Annual Family Income: Rs. 1,50,000/- (One Lakh Fifty Thousand)", fill=(15, 23, 42), font=font)
        draw.text((30, 195), "Valid For: FY 2026-2027", fill=(71, 85, 105), font=font)

    # 2. Render Overlay Highlights, Bounding Boxes & Step Badges
    callout_colors = [
        ((220, 38, 38), (254, 226, 226)),   # Red
        ((37, 99, 235), (219, 234, 254)),   # Blue
        ((217, 119, 6), (254, 243, 199)),   # Amber
    ]

    for idx, c in enumerate(card.bounding_box_callouts):
        # Scale percentage coordinates to canvas pixels
        bx = int(c.x * width / 100.0)
        by = int(c.y * height / 100.0)
        bw = int(c.width * width / 100.0)
        bh = int(c.height * height / 100.0)

        stroke_color, fill_tint = callout_colors[idx % len(callout_colors)]

        # Highlight bounding box
        draw.rectangle([(bx, by), (bx + bw, by + bh)], outline=stroke_color, width=3)

        # Callout badge indicator
        badge_radius = 12
        cx, cy = max(bx - 10, 16), max(by - 10, 16)
        draw.ellipse([(cx - badge_radius, cy - badge_radius), (cx + badge_radius, cy + badge_radius)], fill=stroke_color)
        draw.text((cx - 4, cy - 6), str(idx + 1), fill=(255, 255, 255), font=font)

        # Label tag above/below box
        label_text = f"#{idx + 1}: {c.label}"
        tag_x = bx + 5
        tag_y = max(by - 18, 14)
        draw.rectangle([(tag_x - 3, tag_y - 2), (tag_x + len(label_text) * 7 + 6, tag_y + 12)], fill=stroke_color)
        draw.text((tag_x, tag_y), label_text, fill=(255, 255, 255), font=font)

    # Export to base64 PNG
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64_data}"


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
        try:
            card_copy.annotated_image_url = render_synthetic_annotated_image(card_copy)
        except Exception:
            card_copy.annotated_image_url = None
        return card_copy

    return None
