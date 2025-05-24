import os
import json
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

load_dotenv()

RESEND_API_KEY: Optional[str] = os.getenv("RESEND_API_KEY")
GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
PERPLEXITY_API_KEY: Optional[str] = os.getenv("PERPLEXITY_API_KEY")

GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")
DEFAULT_FROM_EMAIL: str = os.getenv("DEFAULT_FROM_EMAIL", "MCQS Feedback <code@voting.bartoszbak.org>")
DEFAULT_SUBJECT_LINE: str = os.getenv("DEFAULT_SUBJECT_LINE", "MCQS Feedback")

SEND_EMAIL_RATE_LIMIT: str = os.getenv("SEND_EMAIL_RATE_LIMIT", "25 per day")
EXPLAIN_RATE_LIMIT: str = os.getenv("EXPLAIN_RATE_LIMIT", "75 per day")


app = Flask(__name__)
CORS(app)

limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[])

def _extract_first_text_from_gemini(resp: Dict[str, Any]) -> Optional[str]:
    """Return first non-empty text chunk from Gemini response or None."""

    for cand in resp.get("candidates", []):
        content = cand.get("content") or cand.get("message") or {}
        for part in content.get("parts", []):
            txt = str(part.get("text", "")).strip()
            if txt:
                return txt

    # Some experimental endpoints put text at top level
    top_txt = str(resp.get("text", "")).strip()
    return top_txt or None


def generate_subject_with_gemini(feedback_html: str, api_key: Optional[str]) -> str:
    """Generate a concise subject line using Gemini.

    Falls back to DEFAULT_SUBJECT_LINE if the key isn't configured or on errors.
    """

    if not api_key:
        app.logger.warning("Gemini API key not configured – using default subject line.")
        return DEFAULT_SUBJECT_LINE

    prompt = (
        "Please create a very short and concise email subject line "
        "(max 5-7 words) summarizing the following user feedback. "
        "Only return the subject line text itself, without any prefixes like "
        "'Subject:' or quotation marks.\n\n"
        f"Feedback:\n{feedback_html}"
    )

    gemini_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL_NAME}:generateContent?key={api_key}"
    )

    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 20, "temperature": 0.7},
    }

    try:
        resp = requests.post(
            gemini_url,
            json=payload,
            timeout=10,
            headers={
                "User-Agent": "mcqs-helper/1.0 (Gemini Request)",
            },
        )
        resp.raise_for_status()
        body_json: Dict[str, Any] = resp.json()

        subject = _extract_first_text_from_gemini(body_json)
        if subject:
            return subject
        app.logger.warning("No extractable subject in Gemini response – falling back to default.")
    except requests.RequestException as exc:
        app.logger.error("Error communicating with Gemini: %s", exc)
    except ValueError:
        app.logger.error("Non-JSON response from Gemini.")

    return DEFAULT_SUBJECT_LINE



@app.route("/health", methods=["GET"])
def health():
    """Basic health-check endpoint."""
    return jsonify(status="ok"), 200


@limiter.limit(SEND_EMAIL_RATE_LIMIT)
@app.route("/feedback", methods=["POST"])
def send_feedback():
    """Send email via Resend API with Gemini-generated subject line."""

    if not RESEND_API_KEY:
        return (
            jsonify(error="Server misconfiguration – RESEND_API_KEY is missing"),
            500,
        )

    if not request.is_json:
        return jsonify(error="Request body must be JSON"), 400

    req_body: Dict[str, Any] = request.get_json(silent=True) or {}

    html_content: Optional[str] = req_body.get("html_body")
    to_recipients: Any = req_body.get("to")

    missing_params: List[str] = [
        name for name, val in (("html_body", html_content), ("to", to_recipients)) if not val
    ]
    if missing_params:
        return (
            jsonify(error=f"Missing required parameters: {', '.join(missing_params)}"),
            400,
        )

    if not isinstance(to_recipients, list) or not all(isinstance(x, str) for x in to_recipients):
        return (
            jsonify(error="'to' parameter must be a list of email strings"),
            400,
        )

    subject_line: str = generate_subject_with_gemini(html_content, GEMINI_API_KEY)

    email_payload: Dict[str, Any] = {
        "from": DEFAULT_FROM_EMAIL,
        "to": to_recipients,
        "subject": subject_line,
        "html": html_content,
    }

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            json=email_payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "User-Agent": "mcqs-helper/1.0 (Resend Request)",
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        app.logger.error("Network error contacting Resend: %s", exc)
        return jsonify(error="Network error contacting Resend"), 502

    # On non-2xx, propagate error details if available
    if not (200 <= resp.status_code < 300):
        app.logger.warning(
            "Resend API returned non-2xx (status %s): %s", resp.status_code, resp.text
        )
        return (
            jsonify(
                error="Failed to send email via Resend",
                resend_status=resp.status_code,
                resend_body=resp.text,
            ),
            resp.status_code,
        )

    return jsonify(resp.json()), 200


@limiter.limit(EXPLAIN_RATE_LIMIT)
@app.route("/explain", methods=["POST"])
def explain():
    """Explain why the answer is so, using Perplexity API."""
    return jsonify(status="ok"), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7480)
