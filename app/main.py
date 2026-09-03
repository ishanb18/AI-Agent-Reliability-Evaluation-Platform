import os
import structlog
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.config import settings
from app.db.database import engine, get_db, Base
from app.models.run import Run  # noqa: F401
from app.models.agent import Agent  # noqa: F401 — ensures agents table is created
from app.models.test_suite import TestSuite, TestCase  # noqa: F401 — ensures test tables are created
from app.models.eval_run import EvalRun, EvalRunCase, Evaluation  # noqa: F401 — Day 4: eval tables
from app.models.experiment import Experiment  # noqa: F401 — Day 6: experiments table
from app.models.agent_version import AgentVersion  # noqa: F401 — Day 7: versioning table
from app.models.user import User  # noqa: F401 — User model
from app.providers import ModelGateway, ProviderResponse, ProviderStats
from app.routers import agents as agents_router
from app.routers import test_suites as test_suites_router
from app.routers import evaluations as evaluations_router
from app.routers import experiments as experiments_router
from app.routers import agent_versions as agent_versions_router
from app.routers import auth as auth_router
from app.routers import sdk_demo as sdk_demo_router

# ── Structured Logger ─────────────────────────────────────────────────────────
log = structlog.get_logger()

# ── Global Model Gateway Instance ─────────────────────────────────────────────
gateway = ModelGateway()


# ── Lifespan: runs on startup / shutdown ──────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: Creates PostgreSQL database tables if missing (or SQLite fallback) and initializes Gateway.
    Shutdown: Cleanup logger.
    """
    log.info("startup: creating database tables if they do not exist")
    try:
        Base.metadata.create_all(bind=engine)
        # Ensure status column exists in PostgreSQL
        try:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE test_cases ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'active'"))
                conn.execute(text("ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS version_id INTEGER REFERENCES agent_versions(id) ON DELETE SET NULL"))
                conn.commit()
        except Exception as ex:
            log.info("postgresql column check done", detail=str(ex))
        log.info("startup: database & gateway ready", env=settings.app_env)
    except Exception as e:
        log.warning("postgresql unavailable, falling back to local sqlite engine", error=str(e))
        from sqlalchemy import create_engine
        from app.db import database
        database.engine = create_engine("sqlite:///./evalplatform.db", connect_args={"check_same_thread": False})
        database.SessionLocal.configure(bind=database.engine)
        Base.metadata.create_all(bind=database.engine)
        
        # Ensure status column exists in SQLite
        try:
            with database.engine.connect() as conn:
                conn.execute(text("ALTER TABLE test_cases ADD COLUMN status VARCHAR(20) DEFAULT 'active'"))
                conn.commit()
        except Exception:
            pass  # column already exists

        # Ensure version_id exists in SQLite
        try:
            with database.engine.connect() as conn:
                conn.execute(text("ALTER TABLE eval_runs ADD COLUMN version_id INTEGER REFERENCES agent_versions(id) ON DELETE SET NULL"))
                conn.commit()
        except Exception:
            pass  # column already exists
        log.info("startup: fallback sqlite database ready")
    yield
    log.info("shutdown: cleanup complete")


# ── FastAPI Application ───────────────────────────────────────────────────────
app = FastAPI(
    title="AI Agent Reliability & Evaluation Platform",
    description="Provider-agnostic AI agent evaluation layer with Model Gateway, failover, and telemetry.",
    version="0.7.0",
    lifespan=lifespan,
)

# ── Register Routers ──────────────────────────────────────────────────────────
app.include_router(auth_router.router, prefix="/auth", tags=["Auth"])
app.include_router(agents_router.router, prefix="/agents", tags=["Agents"])
app.include_router(test_suites_router.router, prefix="/test-suites", tags=["Test Suites"])
app.include_router(evaluations_router.router, prefix="/evaluations", tags=["Evaluations"])
app.include_router(experiments_router.router, prefix="/experiments", tags=["Experiments"])
app.include_router(agent_versions_router.router, prefix="/agents", tags=["Agent Versions"])
app.include_router(sdk_demo_router.router, prefix="/sdk", tags=["SDK Playground"])

# ── Request / Response Schemas ────────────────────────────────────────────────
class LLMRequest(BaseModel):
    """Legacy request schema for /test-llm endpoint."""
    prompt: str = Field(description="Input prompt text")
    provider: Optional[str] = Field(default=None, description="Provider choice: gemini, groq, ollama")
    model: Optional[str] = Field(default=None, description="Specific model name (optional)")


class GatewayGenerateRequest(BaseModel):
    """Comprehensive generation request shape for /gateway/generate endpoint."""
    prompt: str = Field(description="Input prompt text to process")
    provider: Optional[str] = Field(default=None, description="Primary provider (default: gemini)")
    model: Optional[str] = Field(default=None, description="Specific model string (overrides job_type if provided)")
    job_type: Optional[str] = Field(
        default=None,
        description="Job role optimization: 'default', 'fast', 'reasoning', 'code'"
    )
    enable_fallback: bool = Field(default=True, description="Enable automatic failover if primary provider fails")
    fallback_order: Optional[List[str]] = Field(
        default=None,
        description="Custom sequence of fallback provider names (e.g. ['groq', 'ollama'])"
    )


class LLMResponse(BaseModel):
    """Standardized response format including DB persistence run_id."""
    run_id: int
    provider: str
    model: str
    prompt: str
    response: Optional[str]
    latency_ms: float
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    status: str
    fallback_used: bool = False
    primary_provider: Optional[str] = None
    error_message: Optional[str] = None


# ── API Endpoints ─────────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
def root():
    """Health check endpoint — verifies application status."""
    return {"status": "ok", "version": "0.6.0", "env": settings.app_env}


@app.get("/providers/status", response_model=Dict[str, ProviderStats], tags=["Providers"])
def providers_status():
    """
    Returns complete real-time telemetry for all LLM providers:
    - Availability & health status (HEALTHY, WARNING, UNAVAILABLE)
    - Request volume, success vs error counts
    - Total input/output tokens used
    - Average latency in milliseconds
    - Supported model mapping (default, fast, reasoning, code)
    """
    return gateway.get_telemetry()


@app.post("/gateway/generate", response_model=LLMResponse, tags=["Gateway"])
def gateway_generate(request: GatewayGenerateRequest, db: Session = Depends(get_db)):
    """
    Model Gateway Endpoint:
    1. Executes generation using primary requested provider or configured default.
    2. Automatically routes model based on job_type ('fast', 'reasoning', 'code', 'default') if model not specified.
    3. Triggers automatic fallback sequence if primary provider encounters errors or rate limits.
    4. Records usage telemetry and persists evaluation run into PostgreSQL.
    """
    log.info(
        "gateway_generate request received",
        provider=request.provider,
        job_type=request.job_type,
        enable_fallback=request.enable_fallback,
    )

    res: ProviderResponse = gateway.generate(
        prompt=request.prompt,
        provider=request.provider,
        model=request.model,
        job_type=request.job_type,
        enable_fallback=request.enable_fallback,
        fallback_order=request.fallback_order,
    )

    # ── Persist to PostgreSQL ─────────────────────────────────────────────────
    run = Run(
        provider=res.provider,
        model=res.model,
        prompt=request.prompt,
        response=res.response_text,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        status=res.status,
        error_message=res.error_message,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    if res.status == "error":
        log.error("gateway generation failed across all attempts", error=res.error_message)
        raise HTTPException(status_code=502, detail=f"Gateway Error: {res.error_message}")

    return LLMResponse(
        run_id=run.id,
        provider=res.provider,
        model=res.model,
        prompt=request.prompt,
        response=res.response_text,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        status=res.status,
        fallback_used=res.fallback_used,
        primary_provider=res.primary_provider,
        error_message=res.error_message,
    )


@app.post("/test-llm", response_model=LLMResponse, tags=["Test"])
def test_llm(request: LLMRequest, db: Session = Depends(get_db)):
    """
    Legacy Day 1 test endpoint — delegates directly to Model Gateway.
    """
    gw_req = GatewayGenerateRequest(
        prompt=request.prompt,
        provider=request.provider,
        model=request.model,
        enable_fallback=True,
    )
    return gateway_generate(request=gw_req, db=db)


# ── Mount React Dashboard Frontend ───────────────────────────────────────────
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="react-assets")

    @app.get("/ui", tags=["Dashboard UI"])
    @app.get("/ui/{path:path}", tags=["Dashboard UI"])
    def serve_react_dashboard(path: str = ""):
        """Serve compiled React SPA dashboard."""
        return FileResponse(os.path.join(frontend_dist, "index.html"))

    @app.get("/", tags=["Root Redirect"])
    def root_redirect():
        """Redirect root URL to Dashboard UI."""
        return FileResponse(os.path.join(frontend_dist, "index.html"))
else:
    # Fallback to app/static if react dist not built yet
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

        @app.get("/ui", tags=["Dashboard UI"])
        def serve_static_dashboard():
            return FileResponse(os.path.join(static_dir, "index.html"))
