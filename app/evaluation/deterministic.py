"""
Deterministic Evaluators — rule-based metric computation with zero LLM cost.

Every function here is pure math or set operations. No LLM is called.
These metrics are always reliable (no hallucination possible) and run in <1ms.

Metrics implemented:
  evaluate_tool_accuracy   → Did the agent call the right tools?
  evaluate_trajectory      → Did the agent call tools in the right order?
  calculate_cost           → How much did this agent call cost (USD)?
  score_latency            → How fast was the response?

Why keep these separate from llm_judge.py?
  - Zero API cost
  - Instant results
  - Deterministic (same input always gives same output)
  - Can run even if all LLM providers are down
"""

import structlog
from typing import List, Optional

log = structlog.get_logger()


# ── Tool Accuracy ─────────────────────────────────────────────────────────────

def evaluate_tool_accuracy(
    actual_tools: List[str],
    expected_tools: List[str],
) -> float:
    """
    Score how accurately the agent selected tools.

    Measures SET correctness: what fraction of the expected tools did the agent call?
    Order does NOT matter here — that's trajectory.

    Formula:
        score = |actual ∩ expected| / |expected|

    Examples:
        expected = ["get_order", "cancel_order"]
        actual   = ["get_order", "cancel_order"]  → score = 2/2 = 1.0  (perfect)
        actual   = ["get_order"]                  → score = 1/2 = 0.5  (missed cancel)
        actual   = ["get_order", "send_email"]    → score = 1/2 = 0.5  (wrong extra tool)
        actual   = ["send_email"]                 → score = 0/2 = 0.0  (completely wrong)

    Args:
        actual_tools:   Tools the agent actually called (from tool_trace).
        expected_tools: Tools the test case says the agent SHOULD call.

    Returns:
        Float between 0.0 and 1.0. Returns 0.0 if expected_tools is empty.
    """
    if not expected_tools:
        # No expected tools defined → can't score
        log.warning("evaluate_tool_accuracy called with empty expected_tools, returning 0.0")
        return 0.0

    actual_set = set(actual_tools)
    expected_set = set(expected_tools)

    # Count how many expected tools the agent actually called
    correct = len(actual_set & expected_set)
    score = correct / len(expected_set)

    log.debug(
        "tool_accuracy computed",
        actual=list(actual_set),
        expected=list(expected_set),
        correct=correct,
        score=round(score, 4),
    )
    return round(score, 4)


# ── Trajectory Score ──────────────────────────────────────────────────────────

def evaluate_trajectory(
    actual_tools: List[str],
    expected_tools: List[str],
) -> float:
    """
    Score whether the agent called tools in the right ORDER.

    Uses Longest Common Subsequence (LCS) to find the longest ordered
    matching subsequence between actual and expected tool call sequences.

    Why LCS instead of exact match?
      - Agents may insert extra steps (diagnostic calls) that are fine
      - LCS rewards partial order correctness
      - Exact match would be too strict (0 or 1 only)

    Formula:
        score = LCS(actual, expected) / len(expected)

    Examples:
        expected = ["get_order", "cancel_order", "send_email"]
        actual   = ["get_order", "cancel_order", "send_email"]  → LCS=3, score=3/3=1.0
        actual   = ["get_order", "send_email", "cancel_order"]  → LCS=2, score=2/3=0.67 (wrong order)
        actual   = ["get_order", "cancel_order"]                → LCS=2, score=2/3=0.67 (missing step)
        actual   = ["send_email"]                               → LCS=1, score=1/3=0.33

    Args:
        actual_tools:   Ordered list of tools agent actually called.
        expected_tools: Ordered list of tools agent SHOULD call.

    Returns:
        Float between 0.0 and 1.0.
    """
    if not expected_tools:
        log.warning("evaluate_trajectory called with empty expected_tools, returning 0.0")
        return 0.0

    # ── LCS via dynamic programming ───────────────────────────────────────────
    # dp[i][j] = length of LCS of actual[:i] and expected[:j]
    m, n = len(actual_tools), len(expected_tools)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if actual_tools[i - 1] == expected_tools[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1   # extend the match
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])  # take the best previous

    lcs_length = dp[m][n]
    score = lcs_length / n  # divide by expected length

    log.debug(
        "trajectory_score computed",
        actual=actual_tools,
        expected=expected_tools,
        lcs_length=lcs_length,
        score=round(score, 4),
    )
    return round(score, 4)


# ── Cost Estimation ───────────────────────────────────────────────────────────

# Pricing table: USD per 1 million tokens (as of mid-2025 — update as prices change)
# Format: { "provider/model": (input_price_per_1M, output_price_per_1M) }
_PRICING_TABLE = {
    # OpenAI
    "openai/gpt-4o":           (5.00,   15.00),
    "openai/gpt-4o-mini":      (0.15,    0.60),
    "openai/gpt-4-turbo":      (10.00,  30.00),

    # Gemini
    "gemini/gemini-1.5-flash": (0.075,  0.30),
    "gemini/gemini-1.5-pro":   (3.50,   10.50),
    "gemini/gemini-3.5-flash": (0.075,  0.30),

    # Groq (free tier approximation — minimal cost in paid plans)
    "groq/llama3-8b-8192":     (0.05,   0.08),
    "groq/llama3-70b-8192":    (0.59,   0.79),
    "groq/mixtral-8x7b-32768": (0.24,   0.24),

    # Anthropic
    "anthropic/claude-3-5-sonnet": (3.00, 15.00),
    "anthropic/claude-3-haiku":    (0.25,  1.25),

    # Ollama (local — always free)
    "ollama/llama3":     (0.0, 0.0),
    "ollama/mistral":    (0.0, 0.0),
    "ollama/phi3":       (0.0, 0.0),
}

# Fallback price when model is not in the table
_DEFAULT_PRICE = (1.00, 2.00)   # conservative estimate for unknown models


def calculate_cost(
    provider: str,
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> float:
    """
    Estimate the USD cost of one LLM call based on token counts.

    Looks up the model in the pricing table using "provider/model" key.
    Falls back to a conservative default if the model isn't listed.

    Formula:
        cost = (input_tokens / 1_000_000) * input_price
             + (output_tokens / 1_000_000) * output_price

    Args:
        provider:      Provider name (e.g. "gemini", "groq", "openai")
        model:         Exact model string (e.g. "gpt-4o", "gemini-1.5-flash")
        input_tokens:  Number of input/prompt tokens used
        output_tokens: Number of output/completion tokens generated

    Returns:
        Estimated cost in USD, rounded to 8 decimal places.
        Returns 0.0 if both token counts are None.
    """
    if input_tokens is None and output_tokens is None:
        return 0.0

    # Build lookup key — try full match first, then partial matches
    lookup_key = f"{provider.lower()}/{model.lower()}"
    input_price, output_price = _PRICING_TABLE.get(lookup_key, _DEFAULT_PRICE)

    # If not found with exact model, try matching by provider/model prefix
    if lookup_key not in _PRICING_TABLE:
        for key in _PRICING_TABLE:
            if key.startswith(f"{provider.lower()}/") and model.lower() in key:
                input_price, output_price = _PRICING_TABLE[key]
                break

    in_tokens = input_tokens or 0
    out_tokens = output_tokens or 0

    cost = (in_tokens / 1_000_000) * input_price + (out_tokens / 1_000_000) * output_price

    log.debug(
        "cost calculated",
        provider=provider,
        model=model,
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        cost_usd=round(cost, 8),
    )
    return round(cost, 8)


# ── Latency Score ─────────────────────────────────────────────────────────────

def score_latency(latency_ms: float, threshold_ms: float = 3000.0) -> float:
    """
    Convert raw latency (milliseconds) into a normalized 0.0–1.0 score.

    Faster responses score higher. The threshold defines what counts as "ideal" (1.0).
    Anything above the threshold is penalized linearly down to 0.0.

    Formula:
        score = max(0.0, 1.0 - (latency_ms / threshold_ms))

    Examples (default threshold = 3000ms):
        latency =    0ms → score = 1.0  (instant)
        latency = 1500ms → score = 0.5  (acceptable)
        latency = 3000ms → score = 0.0  (at threshold, still passes barely)
        latency = 5000ms → score = 0.0  (exceeds threshold, capped at 0)

    Why not just store raw latency_ms?
      We do store it. But having a normalized score allows including latency
      in the overall_score average alongside other 0–1 metrics.

    Args:
        latency_ms:    Raw response time in milliseconds.
        threshold_ms:  What counts as "too slow" (default: 3 seconds).

    Returns:
        Float between 0.0 and 1.0.
    """
    if latency_ms <= 0:
        return 1.0
    score = max(0.0, 1.0 - (latency_ms / threshold_ms))
    return round(score, 4)
