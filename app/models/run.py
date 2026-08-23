import datetime
from sqlalchemy import String, Text, Integer, Float, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Run(Base):
    """
    Represents one complete interaction with an LLM provider.
    Every time we call an LLM (for evaluation or testing), we store it here.

    Table name: runs
    """

    __tablename__ = "runs"

    # ── Primary Key ───────────────────────────────────────────────────────────
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # ── What was sent / received ──────────────────────────────────────────────
    provider: Mapped[str] = mapped_column(String(50))        # "gemini", "groq", "ollama"
    model: Mapped[str] = mapped_column(String(100))          # e.g. "gemini-1.5-flash"
    prompt: Mapped[str] = mapped_column(Text)                # the input we sent
    response: Mapped[str] = mapped_column(Text, nullable=True)  # the output we received

    # ── Performance Metrics ───────────────────────────────────────────────────
    latency_ms: Mapped[float] = mapped_column(Float, nullable=True)   # how long it took
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=True) # tokens in the prompt
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=True)# tokens in the response

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), default="success") # "success" | "error"
    error_message: Mapped[str] = mapped_column(Text, nullable=True)    # if status == "error"

    # ── Timestamps ────────────────────────────────────────────────────────────
    # server_default=func.now() means PostgreSQL sets this automatically.
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
