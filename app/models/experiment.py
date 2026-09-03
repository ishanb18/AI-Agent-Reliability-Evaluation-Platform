"""
Experiment ORM Model — Day 6.

Stores the result of comparing two evaluation runs (baseline vs candidate).
"""

import datetime
import json
from typing import Optional, List

from sqlalchemy import Integer, String, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class Experiment(Base):
    """
    Stores a V1 vs V2 experiment comparison.

    An experiment always compares:
      - baseline_run_id: the "before" evaluation run (V1)
      - candidate_run_id: the "after" evaluation run (V2)

    Both runs must reference the same test suite for the comparison to be fair.

    The verdict field gives the final deployment gate result:
      - "pass":   Candidate meets all thresholds and has no significant regressions
      - "review": Candidate passes thresholds but has some regressions from baseline
      - "fail":   Candidate is below threshold on one or more metrics
    """
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # The two runs being compared
    baseline_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_run_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The verdict: "pass", "review", or "fail"
    verdict: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # Full comparison result stored as JSON
    # Includes: metric_diffs, improvements, regressions, suggestions,
    #           fail_reasons, review_reasons, baseline_summary, candidate_summary
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Thresholds used for this experiment (JSON)
    config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # ── JSON Helpers ──────────────────────────────────────────────────────────

    def get_result(self) -> Optional[dict]:
        """Deserialize the result JSON blob."""
        if self.result:
            try:
                return json.loads(self.result)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    def set_result(self, result_dict: dict):
        """Serialize a dict to the result JSON field."""
        self.result = json.dumps(result_dict)

    def get_config(self) -> Optional[dict]:
        """Deserialize the config JSON blob."""
        if self.config:
            try:
                return json.loads(self.config)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    def set_config(self, config_dict: dict):
        """Serialize a dict to the config JSON field."""
        self.config = json.dumps(config_dict)

    def __repr__(self) -> str:
        return (
            f"<Experiment id={self.id} "
            f"baseline={self.baseline_run_id} "
            f"candidate={self.candidate_run_id} "
            f"verdict={self.verdict}>"
        )
