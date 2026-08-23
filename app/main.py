import time
import structlog
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import engine, get_db, Base
from app.models.run import Run  # noqa: F401  — imported so Base knows about this table

# ── Structured Logger ─────────────────────────────────────────────────────────
# structlog outputs JSON-style logs that are easy to search in production.
log = structlog.get_logger()


# ── Lifespan: runs on startup / shutdown ──────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Code before 'yield' runs at startup.
    Code after 'yield' runs at shutdown.
    We use this to create DB tables automatically on first run.
    """
    log.info("startup: creating database tables if they do not exist")
    # This is equivalent to: CREATE TABLE IF NOT EXISTS runs (...)
    # SQLAlchemy reads all models that inherit from Base and creates their tables.
    Base.metadata.create_all(bind=engine)
    log.info("startup: database ready", env=settings.app_env)
    yield
    log.info("shutdown: cleanup complete")


# ── FastAPI Application ───────────────────────────────────────────────────────
app = FastAPI(
    title="AI Agent Reliability & Evaluation Platform",
    description="Connect your AI agent, run evaluations, compare versions, detect regressions.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Pydantic Schemas (Request / Response shapes) ──────────────────────────────
class LLMRequest(BaseModel):
    """What the caller sends to our /test-llm endpoint."""
    prompt: str
    provider: str = "gemini"          # default to gemini; also accepts "groq"
    model: str = "gemini-3.6-flash"   # a fast, free-tier model


class LLMResponse(BaseModel):
    """What we return to the caller."""
    run_id: int
    provider: str
    model: str
    prompt: str
    response: str
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    status: str


# ── Provider Caller ───────────────────────────────────────────────────────────
def call_gemini(prompt: str, model: str) -> dict:
    """
    Calls Google Gemini and returns a standardized result dict.
    Raises RuntimeError if the call fails.
    """
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    gemini_model = genai.GenerativeModel(model)

    start = time.time()
    result = gemini_model.generate_content(prompt)
    latency_ms = (time.time() - start) * 1000

    response_text = result.text
    # Gemini returns token counts in usage_metadata
    usage = getattr(result, "usage_metadata", None)
    input_tokens = getattr(usage, "prompt_token_count", None)
    output_tokens = getattr(usage, "candidates_token_count", None)

    return {
        "response": response_text,
        "latency_ms": round(latency_ms, 2),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def call_groq(prompt: str, model: str) -> dict:
    """
    Calls Groq and returns a standardized result dict.
    """
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)

    start = time.time()
    chat_completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=model,
    )
    latency_ms = (time.time() - start) * 1000

    response_text = chat_completion.choices[0].message.content
    usage = chat_completion.usage
    input_tokens = usage.prompt_tokens if usage else None
    output_tokens = usage.completion_tokens if usage else None

    return {
        "response": response_text,
        "latency_ms": round(latency_ms, 2),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    """Health check — confirms the server is running."""
    return {"status": "ok", "version": "0.1.0", "env": settings.app_env}


@app.get("/providers/status", tags=["Providers"])
def providers_status():
    """Shows which provider API keys are configured."""
    return {
        "gemini": bool(settings.gemini_api_key),
        "groq": bool(settings.groq_api_key),
    }


@app.post("/test-llm", response_model=LLMResponse, tags=["Test"])
def test_llm(request: LLMRequest, db: Session = Depends(get_db)):
    """
    Day 1 core endpoint:
    1. Receives a prompt + provider choice.
    2. Calls the LLM provider.
    3. Stores the run (including latency + tokens) in PostgreSQL.
    4. Returns the result.

    This proves the full stack works end-to-end before we add evaluation logic.
    """
    log.info("test_llm called", provider=request.provider, model=request.model)

    # ── Call the correct provider ─────────────────────────────────────────────
    try:
        if request.provider == "gemini":
            if not settings.gemini_api_key:
                raise HTTPException(status_code=400, detail="GEMINI_API_KEY not configured")
            result = call_gemini(request.prompt, request.model)

        elif request.provider == "groq":
            if not settings.groq_api_key:
                raise HTTPException(status_code=400, detail="GROQ_API_KEY not configured")
            result = call_groq(request.prompt, request.model)

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider '{request.provider}'. Use 'gemini' or 'groq'."
            )

        status = "success"
        error_message = None

    except HTTPException:
        raise  # re-raise FastAPI errors as-is

    except Exception as e:
        # Provider call failed — record the error but don't crash
        log.error("provider call failed", provider=request.provider, error=str(e))
        result = {"response": None, "latency_ms": 0, "input_tokens": None, "output_tokens": None}
        status = "error"
        error_message = str(e)

    # ── Persist to PostgreSQL ─────────────────────────────────────────────────
    run = Run(
        provider=request.provider,
        model=request.model,
        prompt=request.prompt,
        response=result["response"],
        latency_ms=result["latency_ms"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        status=status,
        error_message=error_message,
    )
    db.add(run)
    db.commit()
    db.refresh(run)  # re-reads from DB to get the auto-generated id and created_at

    log.info(
        "run saved",
        run_id=run.id,
        latency_ms=run.latency_ms,
        status=run.status,
    )

    if status == "error":
        raise HTTPException(status_code=502, detail=f"Provider error: {error_message}")

    return LLMResponse(
        run_id=run.id,
        provider=run.provider,
        model=run.model,
        prompt=run.prompt,
        response=run.response,
        latency_ms=run.latency_ms,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        status=run.status,
    )
