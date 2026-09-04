import unittest
from unittest.mock import patch

from app import app
from src.helper import ServiceUnavailableError


class ChatbotRouteTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app.test_client()

    def test_index_renders_without_external_services(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Game Scout", response.data)

    def test_health_does_not_initialize_external_services(self):
        with patch("app.get_service_status", return_value={
            "embeddings": "lazy",
            "pinecone": "unconfigured",
            "ollama": "lazy",
        }):
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

    def test_free_fire_review_is_returned_and_history_is_saved(self):
        history = [
            {"role": "user", "content": "I enjoy fast battle royale games."},
            {"role": "assistant", "content": "I can help compare them."},
            {"role": "user", "content": "Review Free Fire."},
            {
                "role": "assistant",
                "content": "Free Fire is a fast, accessible battle royale with short matches.",
            },
        ]

        with patch(
            "app.chatbot",
            return_value=(
                "Free Fire is a fast, accessible battle royale with short matches.",
                history,
            ),
        ) as mocked_chatbot:
            response = self.client.post(
                "/get",
                json={
                    "msg": "Give me a concise review of Free Fire: gameplay, strengths, weaknesses, and who it suits."
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ok"], True)
        self.assertIn("Free Fire", response.get_json()["reply"])
        mocked_chatbot.assert_called_once()

        with self.client.session_transaction() as saved_session:
            self.assertEqual(saved_session["chat_history"], history)

    def test_service_failure_returns_safe_retryable_error(self):
        with patch(
            "app.chatbot",
            side_effect=ServiceUnavailableError("private provider detail"),
        ):
            response = self.client.post("/get", json={"msg": "Review Free Fire"})

        self.assertEqual(response.status_code, 503)
        payload = response.get_json()
        self.assertEqual(payload["error"]["code"], "service_unavailable")
        self.assertTrue(payload["error"]["retryable"])
        self.assertNotIn("private provider detail", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
