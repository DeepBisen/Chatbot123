# Game Scout

Game Scout is a small Flask chatbot for game recommendations and concise reviews. It uses Pinecone for retrieval, Hugging Face sentence embeddings, and a local Ollama model for responses.

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and add your Pinecone key. Never commit `.env`.
4. Make sure the Pinecone index exists. The default index name is `gamerecommendationsystem`.
5. Install and start Ollama, then download the configured model:

   ```powershell
   ollama pull llama2
   ollama serve
   ```

6. Start the app:

   ```powershell
   python app.py
   ```

7. Open `http://127.0.0.1:10000`.

## Useful checks

- `GET /health` checks that Flask is running and reports whether Pinecone is configured.
- `POST /get` accepts JSON such as `{ "msg": "Review Free Fire" }`.
- A first chat request may download the `sentence-transformers/all-MiniLM-L6-v2` embedding model.

## Example prompt

```text
Give me a concise review of Free Fire: gameplay, strengths, weaknesses, and who it suits.
```

The live response requires all three services to be available: the embedding model, the Pinecone index, and Ollama.
