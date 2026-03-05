# Starchild Concierge API

Knowledge-base chatbot powered by your GitHub repo content, with session memory stored in PostgreSQL.

## Features

- **GitHub-synced knowledge** — auto-fetches `.md` files from your repo as the knowledge base
- **Session memory** — multi-turn conversations persisted in PostgreSQL
- **30-turn limit** per session (configurable)
- **LLM via OpenRouter** — defaults to `google/gemini-3.1-flash-lite-preview`
- **Streaming support** — SSE streaming responses

## Quick Start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Configure .env
cp .env.example .env
# Fill in: GITHUB_TOKEN, OPENROUTER_API_KEY, DATABASE_URL

# 3. Run
python api.py
```

## API

### `POST /chat`

```bash
# New session (auto-creates)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is Starchild?"}'

# Continue existing session
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me more", "session_id": "abc-123"}'

# Streaming
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is Starchild?", "stream": true}'
```

**Response:**
```json
{
  "reply": "Starchild is...",
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "turn": 1,
  "turns_remaining": 29,
  "model": "google/gemini-3.1-flash-lite-preview"
}
```

When a session reaches the turn limit, the API returns `429` with a message to start a new session.

### `GET /sessions/{session_id}`

Retrieve full session history and metadata.

### `GET /knowledge/refresh`

Force re-fetch knowledge files from GitHub.

### `GET /health`

Liveness check with knowledge and DB status.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | ✅ | — | OpenRouter API key |
| `GITHUB_TOKEN` | ✅ | — | GitHub PAT for repo access |
| `DATABASE_URL` | ✅ | — | PostgreSQL connection string |
| `MODEL` | — | `google/gemini-3.1-flash-lite-preview` | LLM model |
| `MAX_TURNS` | — | `30` | Max conversation turns per session |
| `KNOWLEDGE_TTL` | — | `300` | Seconds before re-fetching knowledge |
| `KNOWLEDGE_FILES` | — | `welcome-knowledge-v2.md` | Comma-separated knowledge files |

## Database

The API auto-creates two tables on startup:

- `sessions` — session metadata and turn counter
- `messages` — full conversation history

No migration tool needed — schema is idempotent (`CREATE IF NOT EXISTS`).
