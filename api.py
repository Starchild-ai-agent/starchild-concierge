"""
Starchild Concierge API
-----------------------
POST /chat              — send a message, get a response (session-based)
GET  /health            — liveness check
GET  /knowledge/refresh — force-reload knowledge from GitHub
GET  /sessions/{id}     — get session history

Environment variables (.env):
  GITHUB_TOKEN        — GitHub PAT for fetching repo contents
  OPENROUTER_API_KEY  — OpenRouter API key
  DATABASE_URL        — PostgreSQL connection string
  MODEL               — defaults to google/gemini-3.1-flash-lite-preview
  GITHUB_OWNER        — repo owner, defaults to Starchild-ai-agent
  GITHUB_REPO         — repo name, defaults to starchild-concierge
  GITHUB_BRANCH       — defaults to main
  KNOWLEDGE_FILES     — comma-separated file names to load
  KNOWLEDGE_TTL       — seconds before re-fetching from GitHub, defaults to 300
  MAX_TURNS           — max conversation turns per session, defaults to 30
"""

import os
import time
import uuid
import base64
import logging
from typing import List, Optional
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
import asyncpg

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
GITHUB_TOKEN       = os.getenv("GITHUB_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DATABASE_URL       = os.getenv("DATABASE_URL", "")
MODEL              = os.getenv("MODEL", "google/gemini-3.1-flash-lite-preview")
GITHUB_OWNER       = os.getenv("GITHUB_OWNER", "Starchild-ai-agent")
GITHUB_REPO        = os.getenv("GITHUB_REPO", "starchild-concierge")
GITHUB_BRANCH      = os.getenv("GITHUB_BRANCH", "main")
KNOWLEDGE_FILES    = os.getenv("KNOWLEDGE_FILES", "welcome-knowledge-v2.md").split(",")
KNOWLEDGE_TTL      = int(os.getenv("KNOWLEDGE_TTL", "300"))
MAX_TURNS          = int(os.getenv("MAX_TURNS", "30"))

# ── Knowledge cache ──────────────────────────────────────────────────────────
_knowledge_cache: dict = {"content": "", "loaded_at": 0}


def fetch_file_from_github(filename: str) -> str:
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{filename.strip()}?ref={GITHUB_BRANCH}"
    )
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    resp = httpx.get(url, headers=headers, timeout=10)
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub API error for {filename}: {resp.status_code} {resp.text[:200]}")

    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8")


def load_knowledge(force: bool = False) -> str:
    now = time.time()
    if not force and _knowledge_cache["content"] and (now - _knowledge_cache["loaded_at"] < KNOWLEDGE_TTL):
        return _knowledge_cache["content"]

    logger.info("Loading knowledge from GitHub...")
    parts = []
    for fname in KNOWLEDGE_FILES:
        try:
            content = fetch_file_from_github(fname)
            parts.append(f"## {fname}\n\n{content}")
            logger.info(f"  ✓ {fname} ({len(content)} chars)")
        except Exception as e:
            logger.warning(f"  ✗ Could not load {fname}: {e}")

    combined = "\n\n---\n\n".join(parts)
    _knowledge_cache["content"] = combined
    _knowledge_cache["loaded_at"] = now
    return combined


def build_system_prompt() -> str:
    knowledge = load_knowledge()
    return f"""You are Starchild — the concierge agent for iamstarchild.com.

Your knowledge base is below. Use it to answer user questions accurately.
If something isn't covered, say so honestly — don't invent details.

─────────────────────────────────────────
KNOWLEDGE BASE
─────────────────────────────────────────
{knowledge}
─────────────────────────────────────────
"""


# ── Database ──────────────────────────────────────────────────────────────────
db_pool: Optional[asyncpg.Pool] = None

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    turn_count    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id            BIGSERIAL PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
"""


async def init_db():
    global db_pool
    if not DATABASE_URL:
        logger.warning("DATABASE_URL not set — session persistence disabled")
        return
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    async with db_pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    logger.info("Database connected and schema ensured")


async def close_db():
    if db_pool:
        await db_pool.close()


async def get_or_create_session(session_id: Optional[str]) -> tuple[str, int]:
    """Returns (session_id, turn_count). Creates session if needed."""
    if not db_pool:
        # No DB — return ephemeral session
        return session_id or str(uuid.uuid4()), 0

    if session_id:
        row = await db_pool.fetchrow(
            "SELECT id, turn_count FROM sessions WHERE id = $1", session_id
        )
        if row:
            return row["id"], row["turn_count"]

    # Create new session
    new_id = session_id or str(uuid.uuid4())
    await db_pool.execute(
        "INSERT INTO sessions (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", new_id
    )
    return new_id, 0


async def load_session_history(session_id: str) -> List[dict]:
    """Load all messages for a session, ordered by time."""
    if not db_pool:
        return []
    rows = await db_pool.fetch(
        "SELECT role, content FROM messages WHERE session_id = $1 ORDER BY created_at",
        session_id
    )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def save_turn(session_id: str, user_content: str, assistant_content: str):
    """Save a user+assistant message pair and bump turn count."""
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES ($1, 'user', $2)",
                session_id, user_content
            )
            await conn.execute(
                "INSERT INTO messages (session_id, role, content) VALUES ($1, 'assistant', $2)",
                session_id, assistant_content
            )
            await conn.execute(
                "UPDATE sessions SET turn_count = turn_count + 1, updated_at = now() WHERE id = $1",
                session_id
            )


# ── FastAPI app ───────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    load_knowledge()
    await init_db()
    logger.info("Starchild Concierge API ready.")
    yield
    await close_db()

app = FastAPI(title="Starchild Concierge API", version="2.0.0", lifespan=lifespan)

client = OpenAI(
    api_key=OPENROUTER_API_KEY or "placeholder",
    base_url="https://openrouter.ai/api/v1",
)


# ── Schemas ───────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str                          # current user message
    session_id: Optional[str] = None      # omit to create new session
    stream: bool = False

class ChatResponse(BaseModel):
    reply: str
    session_id: str
    turn: int               # current turn number (1-based)
    turns_remaining: int
    model: str

class SessionInfo(BaseModel):
    session_id: str
    turn_count: int
    turns_remaining: int
    created_at: Optional[str] = None
    messages: List[dict]


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "knowledge_loaded": bool(_knowledge_cache["content"]),
        "knowledge_age_seconds": int(time.time() - _knowledge_cache["loaded_at"]),
        "model": MODEL,
        "max_turns": MAX_TURNS,
        "db_connected": db_pool is not None,
    }


@app.get("/knowledge/refresh")
async def refresh_knowledge():
    content = load_knowledge(force=True)
    return {
        "status": "refreshed",
        "chars": len(content),
        "files": KNOWLEDGE_FILES,
    }


@app.get("/sessions/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not configured")

    row = await db_pool.fetchrow(
        "SELECT id, turn_count, created_at FROM sessions WHERE id = $1", session_id
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = await load_session_history(session_id)
    return SessionInfo(
        session_id=row["id"],
        turn_count=row["turn_count"],
        turns_remaining=max(0, MAX_TURNS - row["turn_count"]),
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
        messages=messages,
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not set")

    # 1. Session management
    session_id, turn_count = await get_or_create_session(req.session_id)

    if turn_count >= MAX_TURNS:
        raise HTTPException(
            status_code=429,
            detail=f"Session {session_id} has reached the {MAX_TURNS}-turn limit. Please start a new session."
        )

    # 2. Build messages: system + history + current
    system_prompt = build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]

    history = await load_session_history(session_id)
    messages.extend(history)
    messages.append({"role": "user", "content": req.message})

    # 3. Call LLM
    if req.stream:
        async def generate():
            collected = []
            stream = client.chat.completions.create(
                model=MODEL, messages=messages, stream=True
            )
            for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    collected.append(delta)
                    yield delta
            # Save after stream completes
            full_reply = "".join(collected)
            await save_turn(session_id, req.message, full_reply)

        return StreamingResponse(
            generate(),
            media_type="text/plain",
            headers={
                "X-Session-Id": session_id,
                "X-Turn": str(turn_count + 1),
            }
        )

    resp = client.chat.completions.create(model=MODEL, messages=messages)
    reply = resp.choices[0].message.content

    # 4. Persist
    await save_turn(session_id, req.message, reply)

    return ChatResponse(
        reply=reply,
        session_id=session_id,
        turn=turn_count + 1,
        turns_remaining=max(0, MAX_TURNS - turn_count - 1),
        model=MODEL,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
