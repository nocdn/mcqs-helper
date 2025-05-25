import os
import json
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

load_dotenv()

# validate required env vars at startup
def validate_required_env_vars():
    """validates that all required environment variables are present. raises error if any are missing."""
    required_vars = ["RESEND_API_KEY", "GEMINI_API_KEY", "PERPLEXITY_API_KEY"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing_vars)}")

validate_required_env_vars()

RESEND_API_KEY: Optional[str] = os.getenv("RESEND_API_KEY")
GEMINI_API_KEY: Optional[str] = os.getenv("GEMINI_API_KEY")
PERPLEXITY_API_KEY: Optional[str] = os.getenv("PERPLEXITY_API_KEY")

GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemini-2.5-flash-preview-05-20")
DEFAULT_FROM_EMAIL: str = os.getenv("DEFAULT_FROM_EMAIL", "MCQS Feedback <code@voting.bartoszbak.org>")
DEFAULT_SUBJECT_LINE: str = os.getenv("DEFAULT_SUBJECT_LINE", "MCQS Feedback")

SEND_EMAIL_RATE_LIMIT: str = os.getenv("SEND_EMAIL_RATE_LIMIT", "25 per day")
EXPLAIN_RATE_LIMIT: str = os.getenv("EXPLAIN_RATE_LIMIT", "75 per day")


app = Flask(__name__)
CORS(app)

limiter = Limiter(key_func=get_remote_address, app=app, default_limits=[])

def _extract_first_text_from_gemini(resp: Dict[str, Any]) -> Optional[str]:
    """extracts first text content from gemini api response json. returns none if no text found."""
    for cand in resp.get("candidates", []):
        content = cand.get("content") or cand.get("message") or {}
        for part in content.get("parts", []):
            txt = str(part.get("text", "")).strip()
            if txt:
                return txt

    top_txt = str(resp.get("text", "")).strip()
    return top_txt or None


def generate_subject_with_gemini(feedback_html: str, api_key: Optional[str]) -> str:
    """generates email subject line using gemini api based on feedback content. falls back to default if api fails or key missing."""
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
        "generationConfig": {"maxOutputTokens": 20, "temperature": 0.5},
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
    """health check endpoint. returns json status ok with 200 code."""
    return jsonify(status="ok"), 200


@limiter.limit(SEND_EMAIL_RATE_LIMIT)
@app.route("/feedback", methods=["POST"])
def send_feedback():
    """sends feedback email via resend api. expects json with html_body and to fields. generates subject with gemini."""
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
    """explains why an answer is correct using perplexity api. expects json with question and correct_answer fields."""
    if not request.is_json:
        return jsonify(error="Request body must be JSON"), 400
    req_body = request.get_json(silent=True) or {}
    question_text = req_body.get("question")
    answer_text = req_body.get("correct_answer")
    missing = [name for name, val in (("question", question_text), ("correct_answer", answer_text)) if not val]
    if missing:
        return jsonify(error=f"Missing required parameters: {', '.join(missing)}"), 400
    prompt = (
        f"Please explain clearly and in simple terms but without being too verbose, "
        f"why the answer to the question {question_text} is {answer_text}. "
        "Do not use markdown. Just plain text"
    )
    payload = {
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
    }
    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            json=payload,
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        app.logger.error("Network error contacting Perplexity: %s", exc)
        return jsonify(error="Network error contacting Perplexity"), 502
    if not (200 <= resp.status_code < 300):
        app.logger.warning("Perplexity API returned non-2xx (status %s): %s", resp.status_code, resp.text)
        return Response(resp.text, status=resp.status_code, mimetype="application/json")
    return Response(resp.text, status=resp.status_code, mimetype="application/json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7480)
