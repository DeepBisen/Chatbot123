from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session

from src.helper import ServiceUnavailableError, chatbot, get_service_status

load_dotenv()

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY")
    or os.getenv("SECRET_KEY")
    or "local-development-key-change-me",
    MAX_CONTENT_LENGTH=16 * 1024,
)

MAX_MESSAGE_LENGTH = 1200
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_ENTRY_LENGTH = 800


def _message_from_request() -> str:
    """Read the current message from JSON or the legacy form payload."""
    payload: Any = request.get_json(silent=True)
    value: Any

    if isinstance(payload, dict) and "msg" in payload:
        value = payload.get("msg")
    else:
        value = request.form.get("msg", "")

    return value.strip() if isinstance(value, str) else ""


def _clean_history(value: Any) -> list[dict[str, str]]:
    """Keep only the small, server-owned history needed by the prompt."""
    if not isinstance(value, list):
        return []

    cleaned: list[dict[str, str]] = []
    for item in value[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue

        cleaned.append(
            {
                "role": role,
                "content": content.strip()[:MAX_HISTORY_ENTRY_LENGTH],
            }
        )

    return cleaned


def _error_response(
    code: str,
    message: str,
    status_code: int,
    retryable: bool,
):
    return (
        jsonify(
            ok=False,
            error={
                "code": code,
                "message": message,
                "retryable": retryable,
            },
        ),
        status_code,
    )


@app.get("/")
def index():
    return render_template("chat.html")


@app.get("/health")
def health():
    """Report process/configuration health without loading model services."""
    services = get_service_status()
    status = "ready" if services["pinecone"] == "configured" else "degraded"
    return jsonify(ok=True, status=status, services=services)


@app.post("/get")
def chat():
    message = _message_from_request()

    if not message:
        return _error_response(
            "empty_message",
            "Tell me what kind of game you want to discover.",
            400,
            False,
        )

    if len(message) > MAX_MESSAGE_LENGTH:
        return _error_response(
            "message_too_long",
            f"Please keep your message under {MAX_MESSAGE_LENGTH} characters.",
            400,
            False,
        )

    history = _clean_history(session.get("chat_history", []))

    try:
        result, updated_history = chatbot(message, history)
    except ServiceUnavailableError:
        logger.warning("Game chatbot services are unavailable")
        return _error_response(
            "service_unavailable",
            "I cannot reach the game data right now. Check the service settings and try again.",
            503,
            True,
        )
    except Exception:
        logger.exception("Unexpected game chatbot failure")
        return _error_response(
            "request_failed",
            "Something went wrong while creating that answer. Try again shortly.",
            500,
            True,
        )

    reply = str(result).strip()
    if not reply:
        return _error_response(
            "request_failed",
            "I could not create an answer for that request. Try again shortly.",
            502,
            True,
        )

    session["chat_history"] = _clean_history(updated_history)
    return jsonify(ok=True, reply=reply)


@app.errorhandler(413)
def request_entity_too_large(_error):
    return _error_response(
        "message_too_large",
        "That request is too large. Please shorten your message and try again.",
        413,
        False,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
