from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_INDEX_NAME = "gamerecommendationsystem"
DEFAULT_OLLAMA_MODEL = "llama2"
MAX_CONTEXT_CHARS = 3070
MAX_HISTORY_MESSAGES = 8
MAX_HISTORY_ENTRY_LENGTH = 800


class ServiceUnavailableError(RuntimeError):
    """Raised when an external model or retrieval service cannot be used."""


@lru_cache(maxsize=1)
def get_embeddings():
    """Create the embedding model only when a chat request needs it."""
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except Exception as exc:
        raise ServiceUnavailableError(
            "The Hugging Face embedding integration is not installed."
        ) from exc

    try:
        return HuggingFaceEmbeddings(
            model_name=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            encode_kwargs={"normalize_embeddings": True},
        )
    except Exception as exc:
        raise ServiceUnavailableError(
            "The embedding model could not be loaded."
        ) from exc


@lru_cache(maxsize=1)
def get_index():
    """Create the Pinecone index client only when retrieval is requested."""
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise ServiceUnavailableError(
            "Pinecone is not configured for this environment."
        )

    try:
        from pinecone import Pinecone

        client = Pinecone(api_key=api_key)
        return client.Index(os.getenv("PINECONE_INDEX_NAME", DEFAULT_INDEX_NAME))
    except Exception as exc:
        raise ServiceUnavailableError(
            "The game data index could not be reached."
        ) from exc


@lru_cache(maxsize=1)
def get_llm():
    """Create the local Ollama client only when a response is needed."""
    try:
        from langchain_ollama import OllamaLLM
    except Exception as exc:
        raise ServiceUnavailableError(
            "The Ollama integration is not installed."
        ) from exc

    try:
        return OllamaLLM(
            model=os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        )
    except Exception as exc:
        raise ServiceUnavailableError(
            "The local language model could not be initialized."
        ) from exc


def get_service_status() -> dict[str, str]:
    """Return lightweight status data without contacting external services."""
    return {
        "embeddings": "lazy",
        "pinecone": "configured"
        if os.getenv("PINECONE_API_KEY")
        else "unconfigured",
        "ollama": "lazy",
    }


def load_file(path: str):
    """Load source documents for the optional indexing pipeline."""
    try:
        from langchain_community.document_loaders.csv_loader import CSVLoader

        return CSVLoader(file_path=path, encoding="utf-8").load()
    except Exception as exc:
        raise ServiceUnavailableError("The game data file could not be loaded.") from exc


def text_split(extracted_data):
    """Split source documents without importing LangChain during app startup."""
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except Exception:
        try:
            from langchain.text_splitter import RecursiveCharacterTextSplitter
        except Exception as exc:
            raise ServiceUnavailableError(
                "The text splitting dependency is not installed."
            ) from exc

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=20)
    return splitter.split_documents(extracted_data)


def _clean_history(conversation_history: Any) -> list[dict[str, str]]:
    if not isinstance(conversation_history, list):
        return []

    cleaned: list[dict[str, str]] = []
    for message in conversation_history[-MAX_HISTORY_MESSAGES:]:
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue

        cleaned.append(
            {
                "role": role,
                "content": content.strip()[:MAX_HISTORY_ENTRY_LENGTH],
            }
        )

    return cleaned


def _retrieval_payload(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        result = result.to_dict()
    return result if isinstance(result, dict) else {}


def retrieve(query: str, conversation_history: list[dict[str, str]]) -> str:
    """Retrieve relevant game notes and build a bounded plain-text prompt."""
    history = _clean_history(conversation_history)
    history_lines = [
        f"{message['role'].capitalize()}: {message['content']}" for message in history
    ]

    try:
        vector = get_embeddings().embed_query(query)
        result = get_index().query(
            vector=vector,
            top_k=3,
            include_values=False,
            include_metadata=True,
        )
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        raise ServiceUnavailableError(
            "The game data lookup failed."
        ) from exc

    matches = _retrieval_payload(result).get("matches", [])
    contexts: list[str] = []
    for match in matches if isinstance(matches, list) else []:
        if not isinstance(match, dict):
            continue
        metadata = match.get("metadata")
        if not isinstance(metadata, dict):
            continue
        text = metadata.get("text")
        if isinstance(text, str) and text.strip():
            contexts.append(text.strip())

    context_text = "\n\n---\n\n".join(contexts)
    context_text = context_text[:MAX_CONTEXT_CHARS] or "No matching game notes were found."
    history_text = "\n".join(history_lines) or "No previous conversation."

    return (
        "You are Game Scout, a helpful assistant for game recommendations and reviews. "
        "Use the retrieved game notes when they are relevant. If the notes do not contain "
        "the answer, say what you do and do not know instead of inventing details. "
        "Answer in concise plain text. For a review, cover gameplay, strengths, weaknesses, "
        "and who the game suits.\n\n"
        f"Conversation history:\n{history_text}\n\n"
        f"Retrieved game notes:\n{context_text}\n\n"
        f"Question: {query}\n"
        "Answer:"
    )


def complete(prompt: str) -> str:
    try:
        model = get_llm()
        response = model.invoke(prompt) if hasattr(model, "invoke") else model(prompt)
    except ServiceUnavailableError:
        raise
    except Exception as exc:
        raise ServiceUnavailableError(
            "The local language model did not return an answer."
        ) from exc

    if isinstance(response, str):
        return response.strip()
    return str(response).strip()


def chatbot(
    query: str, chat_history: list[dict[str, str]] | None = None
) -> tuple[str, list[dict[str, str]]]:
    """Return an answer and the bounded conversation history."""
    normalized_query = query.strip()
    history = _clean_history(chat_history or [])
    prompt = retrieve(normalized_query, history)
    response = complete(prompt)

    updated_history = history + [
        {"role": "user", "content": normalized_query},
        {"role": "assistant", "content": response},
    ]
    return response, updated_history[-MAX_HISTORY_MESSAGES:]


# Preserve the original public helper name for indexing scripts.
def download_hugging_face_embedding():
    return get_embeddings()
