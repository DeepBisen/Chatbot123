from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_from_directory,
    session,
)

from src.helper import (
    ServiceUnavailableError,
    chatbot,
    get_game_details,
    get_service_status,
    recommend_games,
    search_games,
)

load_dotenv()

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY")
    or os.getenv("SECRET_KEY")
    or "local-development-key-change-me",
    MAX_CONTENT_LENGTH=16 * 1024,
)

MAX_MESSAGE_LENGTH = 1200
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_ENTRY_LENGTH = 800


@app.get("/style.css")
def style_sheet():
    """Serve the stylesheet locally; Vercel serves public assets from its CDN."""
    return send_from_directory(os.path.join(app.root_path, "public"), "style.css")


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
    status = "ready" if services.get("igdb") == "configured" else "degraded"
    return jsonify(ok=True, status=status, services=services)


def _catalog_limit(value: Any) -> int:
    try:
        return max(1, min(int(value), 12))
    except (TypeError, ValueError):
        return 5


def _catalog_failure():
    logger.warning("IGDB structured catalog request failed")
    return _error_response(
        "service_unavailable",
        "The verified game catalog is temporarily unavailable. Try again shortly.",
        503,
        True,
    )


@app.get("/api/games/search")
def game_search():
    query = request.args.get("q", "").strip()[:120]
    try:
        return jsonify(ok=True, games=search_games(query, _catalog_limit(request.args.get("limit"))))
    except ServiceUnavailableError:
        return _catalog_failure()
    except Exception:
        logger.exception("Unexpected IGDB search failure")
        return _catalog_failure()


@app.get("/api/games/<int:game_id>")
def game_details(game_id: int):
    try:
        game = get_game_details(game_id)
    except ServiceUnavailableError:
        return _catalog_failure()
    except Exception:
        logger.exception("Unexpected IGDB details failure")
        return _catalog_failure()

    if not game:
        return _error_response("game_not_found", "That game was not found in the verified catalog.", 404, False)
    return jsonify(ok=True, game=game)


@app.post("/api/games/recommend")
def game_recommendations():
    payload: Any = request.get_json(silent=True) or {}
    prompt = payload.get("prompt") if isinstance(payload, dict) else ""
    if not isinstance(prompt, str) or not prompt.strip():
        return _error_response("empty_prompt", "Tell us what kind of game you want to find.", 400, False)

    try:
        limit_value = payload.get("limit", 8) if isinstance(payload, dict) else 8
        games = recommend_games(prompt[:MAX_MESSAGE_LENGTH], _catalog_limit(limit_value))
        return jsonify(ok=True, games=games, prompt=prompt.strip())
    except ServiceUnavailableError:
        return _catalog_failure()
    except Exception:
        logger.exception("Unexpected IGDB recommendation failure")
        return _catalog_failure()


@app.get("/api/saved")
def saved_games():
    saved_ids = session.get("saved_game_ids", [])
    if not isinstance(saved_ids, list):
        saved_ids = []
    return jsonify(ok=True, game_ids=[int(game_id) for game_id in saved_ids if str(game_id).isdigit()])


@app.post("/api/saved/<int:game_id>")
def save_game(game_id: int):
    saved_ids = session.get("saved_game_ids", [])
    if not isinstance(saved_ids, list):
        saved_ids = []
    if game_id not in saved_ids:
        saved_ids.append(game_id)
    session["saved_game_ids"] = saved_ids[-50:]
    session.modified = True
    return jsonify(ok=True, game_ids=session["saved_game_ids"])


@app.delete("/api/saved/<int:game_id>")
def remove_saved_game(game_id: int):
    saved_ids = session.get("saved_game_ids", [])
    if not isinstance(saved_ids, list):
        saved_ids = []
    session["saved_game_ids"] = [saved_id for saved_id in saved_ids if saved_id != game_id]
    session.modified = True
    return jsonify(ok=True, game_ids=session["saved_game_ids"])


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
        logger.warning("IGDB game catalog is unavailable")
        return _error_response(
            "service_unavailable",
            "I cannot reach the verified game catalog right now. Check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in the Vercel environment variables and try again.",
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
