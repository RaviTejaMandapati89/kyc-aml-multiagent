import json
import base64
import os
from PIL import Image
import io
from google import genai

client = genai.Client(
    vertexai=True,
    project="kyc-aml-project-488918",
    location="us-central1"
)

MODEL = "gemini-2.5-flash"


def check_for_real_pii(image_bytes: bytes) -> dict:
    image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')
    prompt = """Look at this image carefully. Determine whether this image 
contains REAL personal information belonging to an actual person.

Real PII includes: a real person's actual name, real date of birth, real address, 
real document number on an actual government-issued ID, real photograph of a 
person's face on an ID.

Synthetic/safe content includes: clearly fake names like John Sample or Test 
User, placeholder numbers like XXXX-XXXX, watermarks saying SPECIMEN or 
SAMPLE or VOID, clearly digitally generated templates with no real data.

Respond in this exact JSON format with no other text:
{
    "contains_real_pii": true or false,
    "confidence": 0.0 to 1.0,
    "reason": "one sentence explanation"
}"""

    response = client.models.generate_content(
        model=MODEL,
        contents=[{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                {"text": prompt}
            ]
        }]
    )

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        return {
            "contains_real_pii": True,
            "confidence": 1.0,
            "reason": "Could not parse response — blocking as precaution"
        }


def analyse_document(image_bytes: bytes, declared_doc_type: str) -> dict:
    image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')
    prompt = f"""You are a document verification specialist at a UK bank.
Analyse this identity document image and assess its validity.

The customer declared this is a: {declared_doc_type}

Check for the following:
1. IMAGE QUALITY — Is it clear, legible, fully visible?
2. DOCUMENT AUTHENTICITY — Does it appear genuine? Signs of tampering?
3. DOCUMENT TYPE — Does it match what was declared?
4. COMPLETENESS — Are all key fields visible? Is it expired?

Respond in this exact JSON format with no other text:
{{
    "image_quality": "Good or Acceptable or Poor",
    "quality_issues": "description or NONE",
    "appears_genuine": true or false,
    "authenticity_concerns": "description or NONE",
    "document_type_match": true or false,
    "all_fields_visible": true or false,
    "expiry_visible": true or false,
    "photograph_present": true or false,
    "overall_result": "PASS or REVIEW or FAIL",
    "overall_reason": "one sentence summary",
    "risk_indicators": []
}}"""

    response = client.models.generate_content(
        model=MODEL,
        contents=[{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                {"text": prompt}
            ]
        }]
    )

    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]
    try:
        result = json.loads(raw.strip())
        result["image_data_retained"] = False
        return result
    except json.JSONDecodeError:
        return {
            "overall_result": "FAIL",
            "overall_reason": "Could not parse response",
            "image_data_retained": False,
            "risk_indicators": ["Parse failure — manual review required"]
        }


def run_document_check(uploaded_file, declared_doc_type: str) -> dict:
    image_bytes = uploaded_file.read()

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
    except Exception:
        return {
            "blocked": True,
            "block_reason": "File does not appear to be a valid image",
            "overall_result": "FAIL"
        }

    image_bytes = uploaded_file.getvalue()

    pii_check = check_for_real_pii(image_bytes)
    if pii_check.get("contains_real_pii") and pii_check.get("confidence", 0) > 0.7:
        return {
            "blocked": True,
            "block_reason": f"Image appears to contain real personal data. {pii_check['reason']} This system must only be used with synthetic or sample documents.",
            "overall_result": "BLOCKED",
            "image_data_retained": False
        }

    analysis = analyse_document(image_bytes, declared_doc_type)
    analysis["blocked"] = False
    del image_bytes
    return analysis
