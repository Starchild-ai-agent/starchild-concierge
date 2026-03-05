"""
Starchild Concierge API
-----------------------
POST /chat  — send messages, get a response from the Starchild concierge.
GET  /health — liveness check.
GET  /knowledge/refresh — force-reload knowledge from GitHub.

Environment variables (.env):
  GITHUB_TOKEN   — GitHub PAT for fetching repo contents
  OPENAI_API_KEY — (or any OpenAI-compatible key)
  OPENAI_BASE_URL — optional, defaults to https://api.openai.com/v1
  MODEL           — defaults to gpt-4o
  GITHUB_OWNER    — repo owner, defaults to Starchild-ai-agent
  GITHUB_REPO     — repo name, defaults to starchild-concierge
  GITHUB_BRANCH   — defaults to main
  KNOWLEDGE_FILES — comma-separated file names to load, defaults to welcome-knowledge-v2.md
  KNOWLEDGE_TTL   — seconds before re-fetching from GitHub, defaults to 300
"""

import os
import time
import base64
import logging
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL           = os.getenv("MODEL", "gpt-4o")
GITHUB_OWNER    = os.getenv("GITHUB_OWNER", "Starchild-ai-agent")
GITHUB_REPO     = os.getenv("GITHUB_REPO", "starchild-concierge")
GITHUB_BRANCH   = os.getenv("GITHUB_BRANCH", "main")
KNOWLEDGE_FILES = os.getenv("KNOWLEDGE_FILES", "welcome-knowledge-v2.md").split(",")
KNOWLEDGE_TTL   = int(os.getenv("KNOWLEDGE_TTL", "300"))  # seconds

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


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Starchild Concierge API", version="1.0.0")

client = OpenAI(api_key=OPENAI_API_KEY or "placeholder", base_url=OPENAI_BASE_URL)


# ── Schemas ───────────────────────────────────────────────────────────────────
class Message(BaseModel):
    role: str          # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    stream: bool = False
    model: Optional[str] = None

class ChatResponse(BaseModel):
    reply: str
    model: str


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {
        "status": "ok",
        "knowledge_loaded": bool(_knowledge_cache["content"]),
        "knowledge_age_seconds": int(time.time() - _knowledge_cache["loaded_at"]),
        "model": MODEL,
    }


@app.get("/knowledge/refresh")
def refresh_knowledge():
    content = load_knowledge(force=True)
    return {
        "status": "refreshed",
        "chars": len(content),
        "files": KNOWLEDGE_FILES,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")

    system_prompt = build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": m.role, "content": m.content} for m in req.messages]

    model = req.model or MODEL

    if req.stream:
        def generate():
            with client.chat.completions.stream(
                model=model, messages=messages
            ) as stream:
                for chunk in stream:
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        yield delta

        return StreamingResponse(generate(), media_type="text/plain")

    resp = client.chat.completions.create(model=model, messages=messages)
    reply = resp.choices[0].message.content
    return ChatResponse(reply=reply, model=resp.model)


# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    load_knowledge()  # pre-warm cache
    logger.info("Starchild Concierge API ready.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
