import boto3
import json
import base64
import os
from PIL import Image
import io

# ── Bedrock client ────────────────────────────────────────────────────────────
bedrock = boto3.client('bedrock-runtime', region_name='eu-west-2')
MODEL_ID = 'eu.anthropic.claude-haiku-4-5-20251001-v1:0'


# ── Layer 2: PII detection — runs before any analysis ────────────────────────

def check_for_real_pii(image_bytes: bytes) -> dict:
    image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')

    prompt = """Look at this image carefully.

Determine whether this image contains REAL personal information belonging to an actual person.

Real PII includes:
- A real person's actual name on an official document
- A real date of birth
- A real address
- A real document number on an actual government-issued ID
- A real photograph of a person's face on an ID document

Synthetic/safe content includes:
- Clearly fake names like "John Sample", "Test User", "Jane Doe"
- Placeholder numbers like "XXXX-XXXX" or "000000000"
- Watermarks saying "SPECIMEN", "SAMPLE", or "VOID"
- Clearly digitally generated or template documents with no real data
- Blank document templates

Respond in this exact JSON format with no other text:
{
    "contains_real_pii": true or false,
    "confidence": 0.0 to 1.0,
    "reason": "one sentence explanation"
}"""

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 200,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        })
    )

    raw = json.loads(response['body'].read())['content'][0]['text'].strip()
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


# ── Main document analysis ────────────────────────────────────────────────────

def analyse_document(image_bytes: bytes, declared_doc_type: str) -> dict:
    # ── Layer 3: Process in memory only, never save to disk ──────────────────
    image_b64 = base64.standard_b64encode(image_bytes).decode('utf-8')

    prompt = f"""You are a document verification specialist at a UK bank.

Analyse this identity document image and assess its validity.

The customer declared this is a: {declared_doc_type}

Check for the following and report on each:

1. IMAGE QUALITY
   - Is the image clear and legible?
   - Is it blurry, pixelated, or too dark/light?
   - Is the full document visible or is it cropped?

2. DOCUMENT AUTHENTICITY
   - Does it appear to be a genuine document or a photocopy/screenshot?
   - Are there visible security features (holograms, watermarks, MRZ strip)?
   - Does the document type match what was declared?
   - Are there signs of tampering, editing, or inconsistency?

3. DOCUMENT COMPLETENESS
   - Are all key fields visible (name, date of birth, document number, expiry)?
   - Is the document expired?
   - Is there a photograph present?

4. OVERALL ASSESSMENT
   - PASS: Document appears genuine, clear, and complete
   - REVIEW: Document has minor issues that need clarification
   - FAIL: Document appears invalid, tampered, or unacceptable

Respond in this exact JSON format with no other text:
{{
    "image_quality": "Good / Acceptable / Poor",
    "quality_issues": "description of any quality issues or NONE",
    "appears_genuine": true or false,
    "authenticity_concerns": "description of concerns or NONE",
    "document_type_match": true or false,
    "all_fields_visible": true or false,
    "expiry_visible": true or false,
    "photograph_present": true or false,
    "overall_result": "PASS or REVIEW or FAIL",
    "overall_reason": "one sentence summary",
    "risk_indicators": ["list", "of", "specific", "concerns"] or []
}}"""

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 800,
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_b64
                        }
                    },
                    {
                        "type": "text",
                        "text": prompt
                    }
                ]
            }]
        })
    )

    raw = json.loads(response['body'].read())['content'][0]['text'].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        result = json.loads(raw.strip())
        # Layer 3: explicitly ensure no image data is returned or stored
        result["image_data_retained"] = False
        return result
    except json.JSONDecodeError:
        return {
            "overall_result": "FAIL",
            "overall_reason": "Could not parse document analysis response",
            "image_data_retained": False,
            "risk_indicators": ["Analysis parse failure — manual review required"]
        }


# ── Public entry point — called from app.py ───────────────────────────────────

def run_document_check(uploaded_file, declared_doc_type: str) -> dict:
    # Read image bytes from Streamlit uploaded file
    image_bytes = uploaded_file.read()

    # Validate it's a real image
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.verify()
    except Exception:
        return {
            "blocked": True,
            "block_reason": "File does not appear to be a valid image",
            "overall_result": "FAIL"
        }

    # Re-read after verify (verify exhausts the stream)
    image_bytes = uploaded_file.getvalue()

    # ── Layer 2: PII check before any analysis ────────────────────────────────
    pii_check = check_for_real_pii(image_bytes)

    if pii_check.get("contains_real_pii") and pii_check.get("confidence", 0) > 0.7:
        return {
            "blocked": True,
            "block_reason": f"Image appears to contain real personal data. {pii_check['reason']} This system must only be used with synthetic or sample documents.",
            "overall_result": "BLOCKED",
            "image_data_retained": False
        }

    # ── Proceed with document analysis ───────────────────────────────────────
    analysis = analyse_document(image_bytes, declared_doc_type)
    analysis["blocked"] = False

    # Layer 3: explicitly clear image bytes from memory
    del image_bytes

    return analysis