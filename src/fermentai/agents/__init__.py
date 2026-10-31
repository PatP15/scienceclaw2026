"""Scientist agents, findings log, offline decision policy and LLM adapter.

Public entry point for the API: ``run_taste_feedback_cycle(feedback_text, context, mode)``.
"""

from .pipeline import run_taste_feedback_cycle

__all__ = ["run_taste_feedback_cycle"]
