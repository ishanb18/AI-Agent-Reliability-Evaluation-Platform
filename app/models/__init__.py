"""
Models package — exports all SQLAlchemy ORM models.

CRITICAL: Every model must be imported here so that Base.metadata.create_all()
discovers and creates all database tables on application startup. If a model
is NOT imported before create_all() runs, its table silently won't be created.
"""

from app.models.run import Run
from app.models.agent import Agent
from app.models.test_suite import TestSuite, TestCase
from app.models.eval_run import EvalRun, EvalRunCase, Evaluation  # Day 4

__all__ = ["Run", "Agent", "TestSuite", "TestCase", "EvalRun", "EvalRunCase", "Evaluation"]
