# Starchild Concierge

Knowledge base for the Starchild welcome agent.

## Purpose

This repository contains the knowledge base used by Starchild's concierge agent — the AI assistant that greets new users and answers questions before they create their first agent.

## Files

- **`welcome-knowledge-v2.md`** — Main knowledge base (the concierge reads this)
- **`api.py`** — FastAPI chatbot API that turns this repo into a callable endpoint
- **`changelog.md`** — Version history

## Usage

The concierge agent loads `welcome-knowledge-v2.md` as its primary knowledge source. Updates to this file are reflected automatically (TTL: 5 minutes, or call `/knowledge/refresh`).

---

## Running the API

### 1. Set environment variables

Copy `.env.example` to `.env` and fill in:

```env
GITHUB_TOKEN=your_github_pat
OPENAI_API_KEY=your_openai_key

# Optional overrides
# MODEL=gpt-4o
# OPENAI_BASE_URL=https://api.openai.com/v1
# KNOWLEDGE_TTL=300
```

### 2. Install & run

```bash
pip install -r requirements.txt
python api.py
```

API is now at `http://localhost:8000`

### 3. Call the API

```bash
# Health check
curl http://localhost:8000/health

# Chat (non-streaming)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "What is Starchild?"}
    ]
  }'

# Chat (streaming)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "How does pricing work?"}
    ],
    "stream": true
  }'

# Force-refresh knowledge from GitHub
curl http://localhost:8000/knowledge/refresh
```

### 4. Deploy (one command)

```bash
# Railway
railway up

# Render — connect the repo in the Render dashboard, set env vars, done.
```

---

## How knowledge updates work

1. Edit `welcome-knowledge-v2.md` in this repo
2. Commit & push
3. API auto-reloads within 5 minutes (configurable via `KNOWLEDGE_TTL`)
4. Or call `GET /knowledge/refresh` to force an immediate reload

No redeploy needed for knowledge updates.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_TOKEN` | — | GitHub PAT (required for private repos) |
| `OPENAI_API_KEY` | — | Your LLM API key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `MODEL` | `gpt-4o` | LLM model to use |
| `GITHUB_OWNER` | `Starchild-ai-agent` | Repo owner |
| `GITHUB_REPO` | `starchild-concierge` | Repo name |
| `GITHUB_BRANCH` | `main` | Branch to read from |
| `KNOWLEDGE_FILES` | `welcome-knowledge-v2.md` | Comma-separated files to load |
| `KNOWLEDGE_TTL` | `300` | Seconds before re-fetching from GitHub |

## Tone

Calm, spacious, futuristic, all-knowing. Not a chatbot — a cosmic intelligence.

## Maintained by

WOO / Starchild team

Last updated: 2026-03-05
