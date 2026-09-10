import os
import unittest
from unittest.mock import patch

from app import app
from src.helper import (
    IgdbClient,
    ServiceUnavailableError,
    _extract_game_query,
    format_game_profile,
    format_search_results,
)


IGDB_GAME = {
    "id": 123,
    "slug": "test-game",
    "name": "Test Game",
    "first_release_date": 1714521600,
    "release_dates": [{"human": "May 1, 2024", "date": 1714521600}],
    "platforms": [{"name": "PC"}],
    "genres": [{"name": "Action"}],
    "themes": [{"name": "Science fiction"}],
    "game_modes": [{"name": "Co-operative"}],
    "player_perspectives": [{"name": "Third person"}],
    "involved_companies": [
        {"company": {"name": "Test Studio"}, "developer": True, "publisher": False, "supporting": False},
        {"company": {"name": "Test Publisher"}, "developer": False, "publisher": True, "supporting": False},
    ],
    "rating": 84.2,
    "rating_count": 1200,
    "aggregated_rating": 80.0,
    "aggregated_rating_count": 25,
    "total_rating": 82.1,
    "total_rating_count": 1225,
    "summary": "A cooperative action adventure.",
    "storyline": "Two players work together through a shared journey.",
    "cover": {"url": "//images.igdb.com/igdb/image/upload/t_cover_big/test.jpg"},
    "screenshots": [{"url": "//images.igdb.com/igdb/image/upload/t_screenshot_med/test.jpg"}],
    "videos": [{"name": "Launch trailer", "video_id": "abc123"}],
    "websites": [{"url": "https://example.test/game", "trusted": True}],
    "external_games": [
        {
            "url": "https://example.test/store",
            "uid": "store-123",
            "external_game_source": {"name": "Example Store"},
        }
    ],
    "game_engines": [{"name": "Test Engine"}],
    "keywords": [{"name": "co-op"}],
    "category": 0,
}


class ChatbotRouteTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app.test_client()

    def test_index_renders_without_external_services(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Game Scout", response.data)
        self.assertIn(b"verified IGDB data", response.data)

    def test_health_requires_twitch_credentials(self):
        with patch("app.get_service_status", return_value={"igdb": "unconfigured"}):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "degraded")

    def test_empty_message_is_rejected(self):
        response = self.client.post("/get", json={"msg": "   "})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "empty_message")

    def test_get_method_is_not_a_chat_request(self):
        response = self.client.get("/get")

        self.assertEqual(response.status_code, 405)

    def test_profile_is_rendered_from_igdb_fields(self):
        with patch.dict(
            os.environ,
            {"TWITCH_CLIENT_ID": "test-client", "TWITCH_CLIENT_SECRET": "test-secret"},
            clear=False,
        ):
            with patch("src.helper.get_igdb") as get_igdb:
                get_igdb.return_value.search.return_value = [IGDB_GAME]
                get_igdb.return_value.details.return_value = IGDB_GAME

                response = self.client.post("/get", json={"msg": "Review Test Game"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertIn("Test Game", payload["reply"])
        self.assertIn("Test Studio", payload["reply"])
        self.assertIn("Co-operative", payload["reply"])
        self.assertIn("https://example.test/store", payload["reply"])
        self.assertIn("https://www.youtube.com/watch?v=abc123", payload["reply"])
        get_igdb.return_value.details.assert_called_once_with(123)

    def test_missing_database_field_is_explicitly_labeled(self):
        reply = format_game_profile({"name": "Unknown Fields", "id": 999})

        self.assertIn("Not available in IGDB data.", reply)
        self.assertNotIn("I think", reply)

    def test_broad_query_returns_labeled_database_matches(self):
        reply = format_search_results(
            "co-op games",
            [{"id": 123, "name": "Test Game", "first_release_date": 1714521600}],
        )

        self.assertIn("IGDB catalog matches", reply)
        self.assertIn("database matches", reply)
        self.assertIn("Test Game", reply)

    def test_igdb_client_refreshes_token_and_sends_apicalypse(self):
        with patch(
            "src.helper._request_json",
            side_effect=[{"access_token": "access-token", "expires_in": 3600}, [IGDB_GAME]],
        ) as request_json:
            client = IgdbClient("test-client", "test-secret")
            results = client.search("Test Game")

        self.assertEqual(results, [IGDB_GAME])
        self.assertEqual(request_json.call_count, 2)
        token_call = request_json.call_args_list[0]
        self.assertEqual(token_call.kwargs["method"], "POST")
        self.assertIn("grant_type=client_credentials", token_call.kwargs["body"])
        igdb_call = request_json.call_args_list[1]
        self.assertEqual(igdb_call.kwargs["method"], "POST")
        self.assertIn('search "Test Game"', igdb_call.kwargs["body"])
        self.assertEqual(igdb_call.kwargs["headers"]["Client-ID"], "test-client")
        self.assertEqual(igdb_call.kwargs["headers"]["Authorization"], "Bearer access-token")

    def test_title_parser_preserves_real_game_title_words(self):
        self.assertEqual(_extract_game_query("Review Test Game"), "Test Game")
        self.assertEqual(
            _extract_game_query("Review Call of Duty: Modern Warfare"),
            "Call of Duty: Modern Warfare",
        )
        self.assertEqual(
            _extract_game_query("Review Free Fire: gameplay and features"),
            "Free Fire",
        )

    def test_structured_game_search_route_returns_json(self):
        with patch("app.search_games", return_value=[{"id": 123, "name": "Test Game"}]) as search:
            response = self.client.get("/api/games/search?q=test&limit=4")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True, "games": [{"id": 123, "name": "Test Game"}]})
        search.assert_called_once_with("test", 4)

    def test_structured_game_details_route_returns_json(self):
        with patch("app.get_game_details", return_value={"id": 123, "name": "Test Game"}) as details:
            response = self.client.get("/api/games/123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["game"]["name"], "Test Game")
        details.assert_called_once_with(123)

    def test_structured_recommendation_route_returns_json(self):
        with patch("app.recommend_games", return_value=[{"id": 123, "name": "Test Game"}]) as recommend:
            response = self.client.post("/api/games/recommend", json={"prompt": "Recommend an RPG", "limit": 8})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(response.get_json()["games"][0]["id"], 123)
        recommend.assert_called_once_with("Recommend an RPG", 8)

    def test_saved_games_are_stored_in_the_flask_session(self):
        response = self.client.post("/api/saved/123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["game_ids"], [123])

        response = self.client.get("/api/saved")
        self.assertEqual(response.get_json()["game_ids"], [123])

        response = self.client.delete("/api/saved/123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["game_ids"], [])

    def test_missing_twitch_credentials_returns_safe_retryable_error(self):
        with patch.dict(
            os.environ,
            {"TWITCH_CLIENT_ID": "", "TWITCH_CLIENT_SECRET": ""},
            clear=False,
        ):
            response = self.client.post("/get", json={"msg": "Review Test Game"})

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "service_unavailable")
        self.assertTrue(payload["error"]["retryable"])
        self.assertNotIn("test-secret", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
