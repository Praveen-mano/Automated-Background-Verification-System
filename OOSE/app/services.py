from __future__ import annotations

import re
import base64
import json
import ssl
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

import certifi
from flask import current_app

from .extensions import db
from .models import (
    Document,
    DocumentAIAnalysis,
    Notification,
    MockAddressRecord,
    MockEducationRecord,
    MockEmploymentRecord,
    MockIdentityRecord,
    Role,
    StageStatus,
    StageType,
    VerificationReport,
    VerificationRequest,
    VerificationStage,
    VerificationStatus,
)

def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


STAGE_SEQUENCE = [
    StageType.IDENTITY,
    StageType.EDUCATION,
    StageType.EMPLOYMENT,
    StageType.ADDRESS,
]


def create_notification(user_ids: list[int], message: str, link: str | None = None) -> None:
    for user_id in sorted(set(user_ids)):
        db.session.add(Notification(user_id=user_id, message=message, link=link))


def create_default_stages(request: VerificationRequest) -> None:
    for stage in STAGE_SEQUENCE:
        db.session.add(
            VerificationStage(
                request_id=request.id,
                stage_type=stage,
                status=StageStatus.PENDING,
            )
        )


def recompute_request_status(
    request: VerificationRequest, generated_by_user_id: int | None = None
) -> tuple[bool, bool]:
    previous_status = request.status
    statuses = [stage.status for stage in request.stages]

    if any(status == StageStatus.REJECTED for status in statuses):
        request.status = VerificationStatus.REJECTED
    elif statuses and all(status == StageStatus.VERIFIED for status in statuses):
        request.status = VerificationStatus.COMPLETED
    elif any(status in {StageStatus.IN_PROGRESS, StageStatus.VERIFIED} for status in statuses):
        request.status = VerificationStatus.IN_PROGRESS
    else:
        request.status = VerificationStatus.PENDING

    if request.status == VerificationStatus.IN_PROGRESS and request.started_at is None:
        request.started_at = utcnow_naive()

    if request.status in {VerificationStatus.COMPLETED, VerificationStatus.REJECTED}:
        request.completed_at = request.completed_at or utcnow_naive()

    status_changed = previous_status != request.status
    report_created = False

    if request.status in {VerificationStatus.COMPLETED, VerificationStatus.REJECTED}:
        if request.report is None:
            generate_report(request, generated_by_user_id)
            report_created = True

    return status_changed, report_created


def generate_report(
    request: VerificationRequest, generated_by_user_id: int | None = None
) -> VerificationReport:
    if request.report:
        return request.report

    candidate = request.candidate_profile
    user = candidate.user

    stage_status_counts = {
        "verified": 0,
        "rejected": 0,
        "in_progress": 0,
        "pending": 0,
    }

    for stage in request.stages:
        stage_status_counts[stage.status.value] = stage_status_counts.get(stage.status.value, 0) + 1

    total_stages = len(request.stages)
    completion_pct = int((stage_status_counts["verified"] / total_stages) * 100) if total_stages else 0

    if request.status == VerificationStatus.COMPLETED:
        recommendation = "Proceed with hiring (all verification stages are cleared)."
        risk_level = "Low"
    elif request.status == VerificationStatus.REJECTED:
        recommendation = "Do not proceed until rejected findings are resolved."
        risk_level = "High"
    else:
        recommendation = "Hold decision until verification is completed."
        risk_level = "Medium"

    lines = [
        "Automated Background Verification Report",
        f"Request #{request.id} | Generated at {utcnow_naive().isoformat(timespec='seconds')} (UTC)",
        "",
        "Executive Summary",
        (
            f"This request is currently marked as '{request.status.value}'. "
            f"Risk level is '{risk_level}'."
        ),
        f"Recommendation: {recommendation}",
        "",
        "Verification Snapshot",
        f"- Total stages reviewed: {total_stages}",
        f"- Verified stages: {stage_status_counts['verified']}",
        f"- Rejected stages: {stage_status_counts['rejected']}",
        f"- Stages in progress: {stage_status_counts['in_progress']}",
        f"- Stages pending: {stage_status_counts['pending']}",
        f"- Overall completion: {completion_pct}%",
        "",
        "Candidate Details",
        f"- Name: {user.full_name}",
        f"- Email: {user.email}",
        f"- Phone: {candidate.phone or 'Not provided'}",
        f"- Identity Number: {candidate.identity_number or 'Not provided'}",
        f"- Address: {candidate.address or 'Not provided'}",
        "",
        "Stage Findings",
    ]

    for stage in request.stages:
        verifier_name = stage.verified_by.full_name if stage.verified_by else "Unassigned"
        verified_at = (
            stage.verified_at.isoformat(timespec="seconds") if stage.verified_at else "N/A"
        )
        lines.extend(
            [
                f"- {stage.stage_type.value.title()}: {stage.status.value}",
                f"  Checked by: {verifier_name}",
                f"  Checked at: {verified_at}",
                f"  Notes: {stage.comments or 'No additional comments'}",
                "",
            ]
        )

    lines.extend(
        [
            "Audit Trail",
            f"- Request created at: {request.created_at.isoformat(timespec='seconds')}",
            f"- Verification started at: {request.started_at.isoformat(timespec='seconds') if request.started_at else 'N/A'}",
            f"- Verification completed at: {request.completed_at.isoformat(timespec='seconds') if request.completed_at else 'N/A'}",
            f"- Generated by user ID: {generated_by_user_id if generated_by_user_id else 'System'}",
            "",
            "This report is system-generated and supports recruitment decision-making.",
        ]
    )

    report = VerificationReport(
        request_id=request.id,
        generated_by_user_id=generated_by_user_id,
        overall_status=request.status,
        summary="\n".join(lines),
    )
    db.session.add(report)
    return report


def role_home_endpoint(role: Role) -> str:
    if role == Role.ADMIN:
        return "admin.dashboard"
    if role == Role.RECRUITER:
        return "recruiter.request_list"
    if role == Role.CANDIDATE:
        return "candidate.profile"
    if role == Role.VERIFIER:
        return "verifier.task_list"
    return "auth.login"


def _extract_text_from_document(document: Document) -> str:
    path = Path(document.storage_path)
    if not path.is_file():
        return ""

    ext = path.suffix.lower()
    if ext == ".txt":
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""

    if ext == ".pdf":
        try:
            import pypdf  # type: ignore

            text_parts: list[str] = []
            reader = pypdf.PdfReader(str(path))
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
            return "\n".join(text_parts)
        except Exception:
            return ""

    if ext in {".png", ".jpg", ".jpeg"}:
        try:
            from PIL import Image  # type: ignore
            import pytesseract  # type: ignore

            return pytesseract.image_to_string(Image.open(path))
        except Exception:
            return ""

    return ""

def _extract_text_with_gemini(path: Path) -> tuple[str, str]:
    api_key = current_app.config.get("GEMINI_API_KEY", "")
    model = current_app.config.get("GEMINI_OCR_MODEL", "gemini-1.5-flash")
    if not api_key:
        return "", ""

    ext = path.suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".pdf"}:
        return "", ""
    if ext == ".png":
        mime = "image/png"
    elif ext in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    else:
        mime = "application/pdf"

    try:
        image_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    except Exception:
        return "", ""

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Extract all readable text from this document. "
                            "Return plain text only."
                        )
                    },
                    {"inline_data": {"mime_type": mime, "data": image_b64}},
                ]
            }
        ]
    }
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    req = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        with urlopen(req, timeout=25, context=ssl_context) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
    except Exception:
        return "", ""

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return (text or "").strip(), json.dumps(data)[:4000]
    except Exception:
        return "", json.dumps(data)[:4000] if isinstance(data, dict) else ""


def extract_text_with_ai(document: Document) -> tuple[str, str, str, str]:
    """Return (provider, model, extracted_text, raw_response)."""
    path = Path(document.storage_path)
    if not path.is_file():
        return "none", "", "", ""

    if current_app.config.get("AI_PROVIDER", "gemini").lower() == "gemini":
        text, raw = _extract_text_with_gemini(path)
        if text:
            return "gemini", current_app.config.get("GEMINI_OCR_MODEL", ""), text, raw

    # Local fallback
    text = _extract_text_from_document(document)
    return "local", "tesseract/pypdf", text, ""


def analyze_document_with_ai(document: Document) -> DocumentAIAnalysis:
    provider, model_name, text, raw = extract_text_with_ai(document)
    text = (text or "").strip()

    # Simple confidence heuristic: longer meaningful extraction => higher confidence.
    token_count = len(_tokens(text))
    confidence = 0.0
    if token_count >= 40:
        confidence = 0.9
    elif token_count >= 20:
        confidence = 0.75
    elif token_count >= 8:
        confidence = 0.55
    elif token_count > 0:
        confidence = 0.35

    needs_manual_review = confidence < 0.7
    summary = (
        f"AI extracted {token_count} tokens using {provider}:{model_name or 'default'}."
        + (" Manual review recommended." if needs_manual_review else " Confidence acceptable.")
    )

    analysis = document.analysis
    if analysis is None:
        analysis = DocumentAIAnalysis(document_id=document.id)
        db.session.add(analysis)

    analysis.provider = provider
    analysis.model_name = model_name or ""
    analysis.extracted_text = text
    analysis.summary = summary
    analysis.confidence = confidence
    analysis.needs_manual_review = needs_manual_review
    analysis.raw_response = raw
    return analysis


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"\W+", (value or "").lower()) if len(token) >= 3]


def _parse_ai_verdict_text(output_text: str) -> tuple[bool, str]:
    if not output_text:
        return False, "AI model returned empty verification response."
    try:
        parsed = json.loads(output_text)
        result = bool(parsed.get("pass"))
        reason = str(parsed.get("reason", "")).strip() or "No reason provided."
        return result, reason
    except Exception:
        lowered = output_text.lower()
        if '"pass": true' in lowered or "pass: true" in lowered:
            return True, output_text[:300]
        return False, output_text[:300]


def _verify_stage_with_ai_model(
    stage, verification_request, corpus: str, registry_context: str
) -> tuple[bool, str]:
    candidate = verification_request.candidate_profile
    user = candidate.user
    stage_label = stage.stage_type.value
    profile_payload = {
        "full_name": user.full_name,
        "identity_number": candidate.identity_number or "",
        "education_details": candidate.education_details or "",
        "employment_details": candidate.employment_details or "",
        "address": candidate.address or "",
    }

    prompt = (
        "You are verifying candidate background documents.\n"
        f"Stage: {stage_label}\n"
        f"Candidate profile fields: {json.dumps(profile_payload)}\n\n"
        f"Mock registry match context: {registry_context}\n\n"
        "Document extracted text corpus:\n"
        f"{corpus[:12000]}\n\n"
        "Return strict JSON with keys:\n"
        "- pass: boolean\n"
        "- reason: short string\n"
        "Decide pass=true only when evidence in documents supports the stage."
    )

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    api_key = current_app.config.get("GEMINI_API_KEY", "")
    model = current_app.config.get("GEMINI_OCR_MODEL", "gemini-1.5-flash")
    if not api_key:
        return False, "GEMINI_API_KEY missing; AI model verification unavailable."

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        f"?key={api_key}"
    )
    req = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=25, context=ssl_context) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
        output_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return _parse_ai_verdict_text(output_text)
    except Exception as exc:
        return False, f"AI model verification request failed: {exc}"


def _extract_stage_registry_id(stage_type: StageType, candidate) -> str:
    if stage_type == StageType.IDENTITY:
        return (candidate.identity_number or "").strip().upper()

    if stage_type == StageType.EDUCATION:
        source = candidate.education_details or ""
        pattern = r"\b(?:EDU|COL|UNI)[-_ ]?\d{3,}\b"
    elif stage_type == StageType.EMPLOYMENT:
        source = candidate.employment_details or ""
        pattern = r"\b(?:EMP|WORK|ORG)[-_ ]?\d{3,}\b"
    elif stage_type == StageType.ADDRESS:
        source = candidate.address or ""
        pattern = r"\b(?:ADDR|ADR|LOC)[-_ ]?\d{3,}\b"
    else:
        return ""

    match = re.search(pattern, source, flags=re.IGNORECASE)
    return (match.group(0).strip().upper().replace(" ", "") if match else "")


def _mock_registry_precheck(stage, verification_request, corpus: str) -> tuple[bool, str]:
    candidate = verification_request.candidate_profile
    record_id = _extract_stage_registry_id(stage.stage_type, candidate)
    if not record_id:
        return (
            False,
            "Profile is missing a stage registry ID. Add IDs like EDU-123, EMP-123, ADDR-123.",
        )

    user = candidate.user
    corpus_l = (corpus or "").lower()

    if stage.stage_type == StageType.IDENTITY:
        record = MockIdentityRecord.query.filter_by(record_id=record_id).first()
        if not record:
            return False, f"Identity registry record '{record_id}' not found."
        checks = [record.record_id, record.candidate_name, record.date_of_birth or "", record.id_type or ""]
    elif stage.stage_type == StageType.EDUCATION:
        record = MockEducationRecord.query.filter_by(record_id=record_id).first()
        if not record:
            return False, f"Education registry record '{record_id}' not found."
        checks = [record.record_id, record.candidate_name, record.institution, record.degree or "", record.graduation_year or ""]
    elif stage.stage_type == StageType.EMPLOYMENT:
        record = MockEmploymentRecord.query.filter_by(record_id=record_id).first()
        if not record:
            return False, f"Employment registry record '{record_id}' not found."
        checks = [record.record_id, record.candidate_name, record.employer, record.designation or ""]
    elif stage.stage_type == StageType.ADDRESS:
        record = MockAddressRecord.query.filter_by(record_id=record_id).first()
        if not record:
            return False, f"Address registry record '{record_id}' not found."
        checks = [record.record_id, record.candidate_name, record.address_line, record.city or "", record.postal_code or ""]
    else:
        return False, "Unsupported stage type."

    expected_name_tokens = _tokens(user.full_name)[:2]
    name_ok = any(token in corpus_l for token in expected_name_tokens) if expected_name_tokens else False
    id_ok = record_id.lower() in corpus_l
    data_checks = [c.strip() for c in checks[2:] if c and c.strip()]
    data_ok = any(fragment.lower() in corpus_l for fragment in data_checks)

    if not id_ok:
        return False, f"Registry ID '{record_id}' not found in uploaded documents."
    if not (name_ok or data_ok):
        return False, "Document text does not match registry values strongly enough."

    return True, f"Registry {record_id} matched against document text."


def verify_stage_with_ai(stage, verification_request) -> tuple[bool, str]:
    docs = verification_request.candidate_profile.documents
    if not docs:
        return False, "No candidate documents found for AI verification."

    extracted_parts = []
    for doc in docs:
        text_parts: list[str] = []

        # Prefer stored AI analysis text first.
        if doc.analysis and doc.analysis.extracted_text:
            cached_text = doc.analysis.extracted_text.strip()
            if cached_text:
                text_parts.append(cached_text)

        # If analysis is empty, attempt active provider extraction.
        if not text_parts:
            _provider, _model, ai_text, _raw = extract_text_with_ai(doc)
            ai_text = (ai_text or "").strip()
            if ai_text:
                text_parts.append(ai_text)

        # Always include local OCR/text extraction as fallback/supplement.
        local_text = _extract_text_from_document(doc).strip()
        if local_text and all(local_text != existing for existing in text_parts):
            text_parts.append(local_text)

        merged_text = "\n".join(part for part in text_parts if part).strip()
        if merged_text:
            extracted_parts.append(merged_text.lower())

    if not extracted_parts:
        return (
            False,
            "Unable to extract text from documents for AI verification.",
        )

    corpus = "\n".join(extracted_parts)
    registry_ok, registry_message = _mock_registry_precheck(stage, verification_request, corpus)
    if not registry_ok:
        return False, registry_message

    ai_ok, ai_message = _verify_stage_with_ai_model(
        stage, verification_request, corpus, registry_context=registry_message
    )
    if not ai_ok and "failed" in ai_message.lower():
        current_app.logger.warning(
            "AI stage verification failed; falling back to OCR/text heuristic: %s",
            ai_message,
        )
    else:
        return ai_ok, f"AI model verdict: {ai_message}"

    extracted_tokens = list(dict.fromkeys(_tokens(corpus)))[:60]
    candidate = verification_request.candidate_profile
    user = candidate.user

    if stage.stage_type == StageType.IDENTITY:
        checks = _tokens(user.full_name)[:2] + _tokens(candidate.identity_number)
    elif stage.stage_type == StageType.EDUCATION:
        checks = _tokens(candidate.education_details)
    elif stage.stage_type == StageType.EMPLOYMENT:
        checks = _tokens(candidate.employment_details)
    elif stage.stage_type == StageType.ADDRESS:
        checks = _tokens(candidate.address)
    else:
        checks = []

    checks = list(dict.fromkeys(checks))[:10]
    current_app.logger.info(
        "AI-HEURISTIC DEBUG | request=%s stage=%s expected_tokens=%s extracted_tokens=%s",
        verification_request.id,
        stage.stage_type.value,
        checks,
        extracted_tokens,
    )
    if not checks:
        return False, "Not enough profile data available to validate this stage."

    matched = [token for token in checks if token in corpus]
    current_app.logger.info(
        "AI-HEURISTIC DEBUG | request=%s stage=%s matched_tokens=%s unmatched_tokens=%s",
        verification_request.id,
        stage.stage_type.value,
        matched,
        [token for token in checks if token not in matched],
    )
    if len(matched) >= max(1, len(checks) // 3):
        return True, f"Heuristic verification passed. Matched tokens: {', '.join(matched[:6])}"
    return False, "Heuristic verification failed. Expected profile tokens were not found in uploaded documents."


def _merge_ai_comment(existing_comment: str | None, ai_comment: str) -> str:
    existing = (existing_comment or "").strip()
    if not existing:
        return ai_comment

    kept_lines = [line for line in existing.splitlines() if not line.startswith("AI pre-check:")]
    kept = "\n".join(kept_lines).strip()
    if not kept:
        return ai_comment
    return f"{kept}\n{ai_comment}"


def apply_ai_preverification(request: VerificationRequest) -> tuple[bool, bool]:
    """
    Run AI-assisted preverification after candidate uploads documents.
    This does not finalize stages; it only adds AI guidance and moves pending
    stages to in_progress so the verifier can make the final decision.
    """
    updated = False
    for stage in request.stages:
        if stage.status in {StageStatus.VERIFIED, StageStatus.REJECTED}:
            continue

        ok, message = verify_stage_with_ai(stage, request)
        ai_comment = (
            f"AI pre-check: {'recommended verified' if ok else 'manual review required'} - {message}"
        )
        merged_comment = _merge_ai_comment(stage.comments, ai_comment)
        if merged_comment != (stage.comments or "").strip():
            stage.comments = merged_comment
            updated = True

        if stage.status == StageStatus.PENDING:
            stage.status = StageStatus.IN_PROGRESS
            updated = True

    status_changed, _ = recompute_request_status(request, generated_by_user_id=None)
    return updated, status_changed


def send_verification_result_emails(request: VerificationRequest) -> tuple[bool, str]:
    if not current_app.config.get("MAIL_ENABLED", False):
        return False, "MAIL_ENABLED is false."

    candidate_user = request.candidate_profile.user
    recruiter_user = request.recruiter
    report_summary = request.report.summary if request.report else "Report not available yet."
    summary_excerpt = report_summary[:2000]
    if len(report_summary) > 2000:
        summary_excerpt += "\n\n[Report truncated in email preview.]"

    recipients = [candidate_user.email, recruiter_user.email]
    sandbox_recipient = (current_app.config.get("MAIL_SANDBOX_RECIPIENT") or "").strip()
    if sandbox_recipient:
        recipients = [sandbox_recipient]
    subject = f"Verification Request #{request.id} - {request.status.value.upper()}"
    body = (
        f"Hello,\n\n"
        f"The verification process for request #{request.id} has been completed with status: "
        f"{request.status.value.upper()}.\n\n"
        f"Candidate: {candidate_user.full_name} ({candidate_user.email})\n"
        f"Recruiter: {recruiter_user.full_name} ({recruiter_user.email})\n\n"
        f"Report Summary:\n"
        f"{summary_excerpt}\n\n"
        f"You can also view/download the full report in the portal.\n\n"
        f"Regards,\nAutomated Verification Platform"
    )

    return send_plain_email(recipients=recipients, subject=subject, body=body)


def send_plain_email(
    recipients: list[str], subject: str, body: str
) -> tuple[bool, str]:
    if not current_app.config.get("MAIL_ENABLED", False):
        return False, "MAIL_ENABLED is false."

    if not recipients:
        return False, "No recipient provided."
    return _send_via_resend_sdk(recipients=recipients, subject=subject, body=body)


def _send_via_resend_sdk(recipients: list[str], subject: str, body: str) -> tuple[bool, str]:
    token = current_app.config.get("MAIL_API_TOKEN", "")
    sender = current_app.config.get("MAIL_FROM")
    if not token:
        return False, "MAIL_API_TOKEN is missing."
    if not sender:
        return False, "MAIL_FROM is missing."

    try:
        import resend  # type: ignore
    except Exception:
        return False, "Resend SDK is not installed. Run: pip install resend"

    try:
        resend.api_key = token
        resend.Emails.send(
            {
                "from": sender,
                "to": recipients,
                "subject": subject,
                "html": f"<pre>{body}</pre>",
            }
        )
        return True, f"Sent via Resend SDK to: {', '.join(recipients)}"
    except Exception as exc:
        return False, f"Mail API (resend) error: {exc}"
