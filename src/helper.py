from __future__ import annotations

import html
import json
import os
import re
import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

DEFAULT_IGDB_API_URL = "https://api.igdb.com/v4"
DEFAULT_TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
DEFAULT_IGDB_PAGE_SIZE = 5
MAX_HISTORY_MESSAGES = 4
MAX_HISTORY_ENTRY_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 2400
UNKNOWN = "Not available in IGDB data."

SEARCH_FIELDS = ",".join(
    [
        "id",
        "name",
        "slug",
        "first_release_date",
        "release_dates.human",
        "platforms.name",
        "genres.name",
        "game_modes.name",
        "rating",
        "rating_count",
        "total_rating",
        "total_rating_count",
        "cover.url",
        "summary",
    ]
)

DETAIL_FIELDS = ",".join(
    [
        "id",
        "name",
        "slug",
        "summary",
        "storyline",
        "first_release_date",
        "release_dates.date",
        "release_dates.human",
        "platforms.name",
        "genres.name",
        "themes.name",
        "game_modes.name",
        "player_perspectives.name",
        "involved_companies.company.name",
        "involved_companies.developer",
        "involved_companies.publisher",
        "involved_companies.supporting",
        "rating",
        "rating_count",
        "aggregated_rating",
        "aggregated_rating_count",
        "total_rating",
        "total_rating_count",
        "cover.url",
        "cover.image_id",
        "screenshots.url",
        "screenshots.image_id",
        "videos.name",
        "videos.video_id",
        "websites.url",
        "websites.trusted",
        "external_games.url",
        "external_games.uid",
        "external_games.external_game_source.name",
        "game_engines.name",
        "keywords.name",
        "category",
    ]
)


class ServiceUnavailableError(RuntimeError):
    """Raised when the hosted game catalog cannot be used."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class IgdbClient:
    """HTTP-only IGDB client using Twitch client-credentials authentication."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        api_url: str = DEFAULT_IGDB_API_URL,
        token_url: str = DEFAULT_TWITCH_TOKEN_URL,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_url = api_url.rstrip("/")
        self.token_url = token_url
        self._access_token: str | None = None
        self._token_expires_at = 0.0

    def _get_access_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token

        payload = _request_json(
            self.token_url,
            method="POST",
            body=urlencode(
                {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                }
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
        if not isinstance(access_token, str) or not access_token.strip():
            raise ServiceUnavailableError("Twitch did not return an access token.")

        self._access_token = access_token
        lifetime = float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0
        self._token_expires_at = time.monotonic() + max(lifetime - 60, 60)
        return access_token

    def _query(self, endpoint: str, query: str, retry: bool = True) -> Any:
        token = self._get_access_token()
        try:
            return _request_json(
                f"{self.api_url}/{endpoint.lstrip('/')}",
                method="POST",
                body=query,
                headers={
                    "Accept": "application/json",
                    "Client-ID": self.client_id,
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "text/plain",
                },
            )
        except ServiceUnavailableError as exc:
            if retry and exc.status_code in {401, 403}:
                self._access_token = None
                self._token_expires_at = 0.0
                return self._query(endpoint, query, retry=False)
            raise

    def search(self, query: str, page_size: int = DEFAULT_IGDB_PAGE_SIZE) -> list[dict[str, Any]]:
        if not query.strip():
            return []

        escaped_query = query.strip().replace("\\", "\\\\").replace('"', '\\"')
        apicalypse = (
            f"fields {SEARCH_FIELDS}; "
            f'search "{escaped_query}"; '
            "where version_parent = null; "
            f"limit {max(1, min(page_size, 10))};"
        )
        payload = self._query("games", apicalypse)
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def discover(self, page_size: int = DEFAULT_IGDB_PAGE_SIZE) -> list[dict[str, Any]]:
        apicalypse = (
            f"fields {SEARCH_FIELDS}; "
            "where version_parent = null & rating_count > 0; "
            "sort rating desc; "
            f"limit {max(1, min(page_size, 10))};"
        )
        payload = self._query("games", apicalypse)
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def details(self, game_id: int | str) -> dict[str, Any]:
        try:
            numeric_id = int(game_id)
        except (TypeError, ValueError) as exc:
            raise ServiceUnavailableError("IGDB did not provide a valid game identifier.") from exc

        payload = self._query(
            "games",
            f"fields {DETAIL_FIELDS}; where id = {numeric_id}; limit 1;",
        )
        if not isinstance(payload, list) or not payload:
            return {}
        return payload[0] if isinstance(payload[0], dict) else {}


def _request_json(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    body: str | bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    request_url = f"{url}?{urlencode(params)}" if params else url
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    request = Request(
        request_url,
        data=body_bytes,
        headers={
            "Accept": "application/json",
            "User-Agent": "GameScout/1.0 (+https://vercel.com)",
            **(headers or {}),
        },
        method=method,
    )

    try:
        with urlopen(request, timeout=15) as response:
            raw_response = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise ServiceUnavailableError("The hosted game catalog rejected the credentials.", exc.code) from exc
        if exc.code == 404:
            raise ServiceUnavailableError("The hosted game catalog endpoint was not found.", exc.code) from exc
        if exc.code == 429:
            raise ServiceUnavailableError("The hosted game catalog rate limit was reached.", exc.code) from exc
        raise ServiceUnavailableError("The hosted game catalog returned an error.", exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ServiceUnavailableError("The hosted game catalog could not be reached.") from exc

    try:
        return json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceUnavailableError("The hosted game catalog returned invalid data.") from exc


@lru_cache(maxsize=1)
def get_igdb() -> IgdbClient:
    """Create the IGDB client only when Twitch credentials are configured."""
    client_id = os.getenv("TWITCH_CLIENT_ID")
    client_secret = os.getenv("TWITCH_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ServiceUnavailableError(
            "TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET are not configured."
        )

    return IgdbClient(
        client_id=client_id,
        client_secret=client_secret,
        api_url=os.getenv("IGDB_API_URL", DEFAULT_IGDB_API_URL),
        token_url=os.getenv("TWITCH_TOKEN_URL", DEFAULT_TWITCH_TOKEN_URL),
    )


def get_service_status() -> dict[str, str]:
    """Return lightweight status data without contacting external services."""
    return {
        "igdb": "configured"
        if os.getenv("TWITCH_CLIENT_ID") and os.getenv("TWITCH_CLIENT_SECRET")
        else "unconfigured",
    }


def _clean_history(value: Any) -> list[dict[str, str]]:
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


def _strip_markup(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    text = html.unescape(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _display(value: Any, fallback: str = UNKNOWN) -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        text = _strip_markup(value)
        return text or fallback
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip():
            result.append(value.strip())
        elif isinstance(value, dict):
            name = value.get("name")
            if isinstance(name, str) and name.strip():
                result.append(name.strip())
    return result


def _list_display(values: Any) -> str:
    names = _names(values)
    return ", ".join(names) if names else UNKNOWN


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _extract_game_query(query: str) -> str:
    """Reduce conversational wording to the likely IGDB search phrase."""
    text = re.sub(r"\s+", " ", query.strip())
    if not text:
        return ""

    patterns = [
        r"^(?:please\s+)?(?:give me|provide|show me)\s+(?:a\s+)?(?:concise\s+|brief\s+|full\s+|detailed\s+)*(?:review|summary|profile|details|information|info|facts)\s+(?:of|about|on|for)\s+",
        r"^(?:review|summarize|profile|details|information|info|facts|features|properties)\s+(?:of|about|on|for)?\s*",
        r"^(?:tell me about|what is|what are the details of|how is)\s+",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)

    similar_match = re.search(r"\b(?:like|similar to)\s+(.+)$", text, flags=re.IGNORECASE)
    if similar_match:
        text = similar_match.group(1)

    text = re.sub(
        r"^(?:please\s+)?(?:recommend|suggest|find)\s+(?:a|an|some|the)?\s*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(?:a|an|the)\s+games?\b",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bgames?\b(?=\s+under\b)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bunder\s+\d+\s*(?:hours?|hrs?)\b", "", text, flags=re.IGNORECASE)
    text = re.split(
        r"\s*:\s*(?=(?:gameplay|strengths|weaknesses|features|properties|platforms|release date|ratings?|who it suits|pros and cons)\b)",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    text = re.split(
        r"\s+(?:gameplay|strengths|weaknesses|features|properties|platforms|release date|ratings?|who it suits|pros and cons)\b",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return text.strip(" .?!,")


def _candidate_score(query: str, candidate: dict[str, Any]) -> float:
    name = _normalize(_display(candidate.get("name"), ""))
    normalized_query = _normalize(query)
    if not name or not normalized_query:
        return 0
    if name == normalized_query or name in normalized_query:
        return 1

    name_tokens = set(name.split())
    query_tokens = set(normalized_query.split())
    return len(name_tokens & query_tokens) / len(name_tokens)


def _select_candidate(query: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not results:
        return None

    ranked = sorted(
        results,
        key=lambda result: _candidate_score(query, result),
        reverse=True,
    )
    best = ranked[0]
    return best if _candidate_score(query, best) >= 0.6 else None


def _specific_game_requested(query: str, candidate: dict[str, Any] | None) -> bool:
    if not candidate:
        return False
    name = _normalize(_display(candidate.get("name"), ""))
    normalized_query = _normalize(query)
    return bool(name and name in normalized_query)


def _format_timestamp(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return UNKNOWN
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return UNKNOWN


def _format_release_dates(game: dict[str, Any]) -> str:
    release_dates = game.get("release_dates")
    values: list[str] = []
    if isinstance(release_dates, list):
        for release in release_dates:
            if not isinstance(release, dict):
                continue
            value = release.get("human") or _format_timestamp(release.get("date"))
            if value and value != UNKNOWN and value not in values:
                values.append(str(value))

    if values:
        return "; ".join(values)
    return _format_timestamp(game.get("first_release_date"))


def _format_involved_companies(values: Any, role: str) -> str:
    if not isinstance(values, list):
        return UNKNOWN

    names: list[str] = []
    for item in values:
        if not isinstance(item, dict) or not item.get(role):
            continue
        company = item.get("company")
        name = company.get("name") if isinstance(company, dict) else None
        if isinstance(name, str) and name.strip() and name.strip() not in names:
            names.append(name.strip())
    return ", ".join(names) if names else UNKNOWN


def _format_platforms(values: Any) -> str:
    return _list_display(values)


def _format_ratings(game: dict[str, Any]) -> str:
    values: list[str] = []
    for label, key in (
        ("IGDB user rating", "rating"),
        ("IGDB critic aggregate", "aggregated_rating"),
        ("IGDB total rating", "total_rating"),
    ):
        if game.get(key) is not None:
            values.append(f"{label}: {game[key]}/100")

    for label, key in (
        ("user ratings", "rating_count"),
        ("critic ratings", "aggregated_rating_count"),
        ("total rating count", "total_rating_count"),
    ):
        if game.get(key) is not None:
            values.append(f"{label}: {game[key]}")

    return "; ".join(values) if values else UNKNOWN


def _image_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return f"https:{value}" if value.startswith("//") else value


def _format_cover(game: dict[str, Any]) -> str:
    cover = game.get("cover")
    url = _image_url(cover.get("url")) if isinstance(cover, dict) else ""
    return url or UNKNOWN


def _format_screenshots(values: Any) -> str:
    if not isinstance(values, list):
        return f"- {UNKNOWN}"
    urls = [_image_url(item.get("url")) for item in values if isinstance(item, dict)]
    urls = [url for url in urls if url]
    return "\n".join(f"- {url}" for url in urls[:12]) or f"- {UNKNOWN}"


def _format_videos(values: Any) -> str:
    if not isinstance(values, list):
        return f"- {UNKNOWN}"
    links: list[str] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        video_id = item.get("video_id")
        name = item.get("name") or "Trailer"
        if isinstance(video_id, str) and video_id.strip():
            links.append(f"{name}: https://www.youtube.com/watch?v={video_id}")
    return "\n".join(f"- {link}" for link in links[:12]) or f"- {UNKNOWN}"


def _format_links(game: dict[str, Any]) -> str:
    links: list[str] = []
    websites = game.get("websites")
    if isinstance(websites, list):
        for website in websites:
            if not isinstance(website, dict):
                continue
            url = website.get("url")
            if isinstance(url, str) and url.strip():
                trusted = "trusted" if website.get("trusted") else "unverified"
                links.append(f"Website ({trusted}): {url.strip()}")

    external_games = game.get("external_games")
    if isinstance(external_games, list):
        for external in external_games:
            if not isinstance(external, dict):
                continue
            url = external.get("url")
            source = external.get("external_game_source")
            source_name = source.get("name") if isinstance(source, dict) else "External store/reference"
            if isinstance(url, str) and url.strip():
                links.append(f"{source_name}: {url.strip()}")

    return "\n".join(f"- {link}" for link in links) or f"- {UNKNOWN}"


def _game_payload(game: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded frontend-safe game object sourced only from IGDB."""
    cover = game.get("cover")
    screenshots = game.get("screenshots")
    videos = game.get("videos")
    websites = game.get("websites")
    external_games = game.get("external_games")

    return {
        "id": game.get("id"),
        "name": _display(game.get("name"), "Untitled game"),
        "slug": _display(game.get("slug"), ""),
        "release_date": _format_release_dates(game),
        "platforms": _names(game.get("platforms")),
        "genres": _names(game.get("genres")),
        "themes": _names(game.get("themes")),
        "modes": _names(game.get("game_modes")),
        "perspectives": _names(game.get("player_perspectives")),
        "developers": _format_involved_companies(game.get("involved_companies"), "developer"),
        "publishers": _format_involved_companies(game.get("involved_companies"), "publisher"),
        "ratings": _format_ratings(game),
        "summary": _strip_markup(game.get("summary")),
        "storyline": _strip_markup(game.get("storyline")),
        "cover_url": _image_url(cover.get("url")) if isinstance(cover, dict) else "",
        "screenshot_urls": [
            _image_url(item.get("url"))
            for item in screenshots[:8]
            if isinstance(item, dict) and _image_url(item.get("url"))
        ] if isinstance(screenshots, list) else [],
        "video_links": [
            {
                "title": item.get("name") or "Trailer",
                "url": f"https://www.youtube.com/watch?v={item.get('video_id')}",
            }
            for item in videos[:6]
            if isinstance(item, dict) and isinstance(item.get("video_id"), str) and item.get("video_id").strip()
        ] if isinstance(videos, list) else [],
        "external_links": (
            [
                {"title": "Website", "url": item.get("url")}
                for item in websites[:6]
                if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("url").strip()
            ] if isinstance(websites, list) else []
        ) + (
            [
                {
                    "title": (
                        item.get("external_game_source", {}).get("name")
                        if isinstance(item.get("external_game_source"), dict)
                        else "External reference"
                    ),
                    "url": item.get("url"),
                }
                for item in external_games[:8]
                if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("url").strip()
            ] if isinstance(external_games, list) else []
        ),
        "source": "IGDB",
    }


def format_game_profile(game: dict[str, Any]) -> str:
    """Render only fields returned by IGDB; never infer missing game facts."""
    summary = _strip_markup(game.get("summary"))
    storyline = _strip_markup(game.get("storyline"))
    if len(summary) > MAX_DESCRIPTION_LENGTH:
        summary = f"{summary[:MAX_DESCRIPTION_LENGTH].rstrip()}…"
    if len(storyline) > MAX_DESCRIPTION_LENGTH:
        storyline = f"{storyline[:MAX_DESCRIPTION_LENGTH].rstrip()}…"

    return "\n".join(
        [
            f"**{_display(game.get('name'))}**",
            "Verified game profile — source: IGDB",
            f"IGDB ID: {_display(game.get('id'))}",
            "",
            f"Release dates: {_format_release_dates(game)}",
            f"Platforms: {_format_platforms(game.get('platforms'))}",
            f"Genres: {_list_display(game.get('genres'))}",
            f"Themes: {_list_display(game.get('themes'))}",
            f"Game modes: {_list_display(game.get('game_modes'))}",
            f"Player perspectives: {_list_display(game.get('player_perspectives'))}",
            f"Developers: {_format_involved_companies(game.get('involved_companies'), 'developer')}",
            f"Publishers: {_format_involved_companies(game.get('involved_companies'), 'publisher')}",
            f"Supporting companies: {_format_involved_companies(game.get('involved_companies'), 'supporting')}",
            f"Ratings: {_format_ratings(game)}",
            f"Game category: {_display(game.get('category'))}",
            f"Game engines: {_list_display(game.get('game_engines'))}",
            f"Keywords: {_list_display(game.get('keywords'))}",
            "",
            "Summary:",
            summary or UNKNOWN,
            "",
            "Storyline:",
            storyline or UNKNOWN,
            "",
            f"Cover image: {_format_cover(game)}",
            "Screenshots:",
            _format_screenshots(game.get("screenshots")),
            "Videos:",
            _format_videos(game.get("videos")),
            "Websites and external links:",
            _format_links(game),
            "",
            "Accuracy note: Every factual field above comes from IGDB. If IGDB did not return a value, it is marked as unavailable instead of being guessed.",
        ]
    )


def format_search_results(query: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return (
            "I could not find a matching game in the IGDB catalog. "
            "Please include the exact game title so I can return a verified profile."
        )

    lines = [
        f"IGDB catalog matches for: {query}",
        "These are database matches, not invented recommendations. Ask for an exact title to get the full verified profile.",
        "",
    ]
    for index, result in enumerate(results, start=1):
        lines.extend(
            [
                f"{index}. **{_display(result.get('name'))}**",
                f"   Release date: {_format_timestamp(result.get('first_release_date'))}",
                f"   Platforms: {_list_display(result.get('platforms'))}",
                f"   Genres: {_list_display(result.get('genres'))}",
                f"   Game modes: {_list_display(result.get('game_modes'))}",
                f"   Rating: {_display(result.get('rating'))}/100",
                f"   IGDB ID: {_display(result.get('id'))}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def chatbot(
    query: str, chat_history: list[dict[str, str]] | None = None
) -> tuple[str, list[dict[str, str]]]:
    """Return a database-grounded profile or a clearly labeled catalog match list."""
    normalized_query = query.strip()
    history = _clean_history(chat_history or [])
    catalog = get_igdb()
    search_query = _extract_game_query(normalized_query) or normalized_query
    results = catalog.search(search_query)

    if not results and search_query != normalized_query:
        results = catalog.search(normalized_query)

    candidate = _select_candidate(search_query, results)
    if _specific_game_requested(normalized_query, candidate):
        game = catalog.details(candidate.get("id"))
        reply = format_game_profile(game) if game else format_search_results(normalized_query, results)
    else:
        reply = format_search_results(normalized_query, results)

    updated_history = history + [
        {"role": "user", "content": normalized_query},
        {"role": "assistant", "content": reply},
    ]
    return reply, _clean_history(updated_history)


def search_games(query: str = "", limit: int = DEFAULT_IGDB_PAGE_SIZE) -> list[dict[str, Any]]:
    """Return structured IGDB cards for Explore and frontend filters."""
    catalog = get_igdb()
    results = catalog.search(query, limit) if query.strip() else catalog.discover(limit)
    return [_game_payload(game) for game in results]


def get_game_details(game_id: int | str) -> dict[str, Any]:
    """Return one structured IGDB profile for the details modal."""
    game = get_igdb().details(game_id)
    return _game_payload(game) if game else {}


def recommend_games(query: str, limit: int = DEFAULT_IGDB_PAGE_SIZE) -> list[dict[str, Any]]:
    """Return structured IGDB matches for frontend Find a Game requests."""
    normalized_query = query.strip()
    search_query = _extract_game_query(normalized_query) or normalized_query
    results = search_games(search_query, limit)
    if not results and search_query != normalized_query:
        results = search_games(normalized_query, limit)
    return results


# Preserve the old public helper name for scripts that may still import it.
def download_hugging_face_embedding():
    raise ServiceUnavailableError(
        "Embedding retrieval was replaced by the IGDB catalog for factual game profiles."
    )
