# Game Scout

Game Scout returns database-grounded game profiles from the hosted IGDB catalog. It does not ask an AI model to invent game facts: responses are rendered directly from IGDB fields, and missing fields are labeled as unavailable.

## Run locally

1. Create and activate a virtual environment.
2. Install the dependencies:

   ```powershell
   python -m pip install -r requirements-local.txt
   ```

3. Copy `.env.example` to `.env` and set `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET`. Never commit `.env`.
4. Start the app:

   ```powershell
   python app.py
   ```

5. Open `http://127.0.0.1:10000`.

The local server is only a development preview. Game data is still fetched from the hosted IGDB API; no local model is used.

## Deploy to Vercel

Vercel detects [`app.py`](./app.py) as the Flask entrypoint. The deployment uses a lightweight HTTP-only client and does not download a local model or embedding model.

Create an application in the [Twitch Developer Console](https://dev.twitch.tv/console/apps), then set these Vercel Production environment variables:

- `TWITCH_CLIENT_ID` — required; Twitch application client ID.
- `TWITCH_CLIENT_SECRET` — required; Twitch application client secret. Store it as a secret.
- `IGDB_API_URL` — optional; defaults to `https://api.igdb.com/v4`.
- `TWITCH_TOKEN_URL` — optional; defaults to `https://id.twitch.tv/oauth2/token`.
- `FLASK_SECRET_KEY` — a long random session secret.

The app exchanges the Twitch credentials for a short-lived access token and refreshes it before expiry. It then queries IGDB with the required `Client-ID` and `Authorization: Bearer` headers.

After adding or changing Vercel environment variables, redeploy the project. Environment variables are injected when the serverless function starts; changing `.env` locally does not update Vercel.

## Response behavior

- A request containing a specific title, such as `Review Free Fire`, searches IGDB and returns a full verified profile.
- The profile includes release dates, platforms, genres, themes, game modes, player perspectives, developers, publishers, supporting companies, ratings, game engines, keywords, summary, storyline, cover, screenshots, videos, websites, and external links when IGDB supplies them.
- A broad request returns clearly labeled IGDB catalog matches instead of fabricated recommendations.
- If IGDB has no value for a field, the response says `Not available in IGDB data.`
- If no title is found, the assistant asks for an exact game title.

## Useful checks

- `GET /health` reports whether both Twitch credentials are configured.
- `POST /get` accepts JSON such as `{ "msg": "Review Free Fire" }`.

## Example prompt

```text
Give me the full verified IGDB profile for Free Fire: release dates, platforms, genres, gameplay modes, developers, publishers, ratings, summary, screenshots, videos, and external links.
```
