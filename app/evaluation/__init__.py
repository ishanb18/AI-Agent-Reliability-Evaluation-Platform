"""
Evaluation package — LLM-as-a-Judge + deterministic evaluators + orchestrator.

Modules in this package:
  llm_judge.py     → qualitative metrics via LLM (correctness, relevance, faithfulness, etc.)
  deterministic.py → quantitative metrics via math (tool accuracy, trajectory, cost, latency)
  orchestrator.py  → coordinates the full evaluation: invoke agent → parse → score → persist
"""
