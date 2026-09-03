"""
Example Script: How a Developer Uses the `evalplatform` Python SDK
------------------------------------------------------------------
Run this script to simulate an external user importing the SDK,
authenticating with their API Key, instrumenting their agent, and running evaluations!
"""

import time
import requests
from app.sdk.evalplatform import EvalClient, trace_step

# 1. Step 1: Get your API Key from your account at http://localhost:8000/ui
# (Once logged in, your API Key is shown in the sidebar & SDK Sandbox tab)
API_KEY = "ant_demo_api_key_123456"
PLATFORM_URL = "http://localhost:8000"

print("===============================================================")
print("[SDK DEMO] Antigravity AI Agent Reliability Platform")
print("===============================================================\n")

# 2. Step 2: Instrument your AI Agent steps with @trace_step
@trace_step("user_intent_parser", step_type="nlu")
def parse_intent(query: str):
    time.sleep(0.04)  # Simulate NLU step
    return {"intent": "support_query", "query": query}

@trace_step("vector_kb_search", step_type="retrieval")
def search_knowledge_base(intent_data: dict):
    time.sleep(0.06)  # Simulate RAG vector retrieval
    return [
        "Doc A: Platform refund policy allows returns within 30 days.",
        "Doc B: Contact support@antigravity.ai for billing help."
    ]

@trace_step("llm_answer_generation", step_type="generation")
def generate_response(query: str, docs: list):
    time.sleep(0.12)  # Simulate LLM response generation
    return f"Based on policy: {docs[0]} For billing questions, contact support."

def my_custom_agent(user_input: str):
    """Main Agent Pipeline entry point."""
    intent = parse_intent(user_input)
    docs = search_knowledge_base(intent)
    answer = generate_response(user_input, docs)
    return {"output": answer, "context": docs}


# 3. Step 3: Define Test Cases for Evaluation
test_cases = [
    {
        "input": "How many days do I have to return an item?",
        "expected_answer": "30 days"
    },
    {
        "input": "Where can I email for billing issues?",
        "expected_answer": "support@antigravity.ai"
    }
]

# 4. Step 4: Initialize SDK Client & Authenticate Account
client = EvalClient(api_key=API_KEY, base_url=PLATFORM_URL)

try:
    print("Step 1: Authenticating SDK with user account API Key...")
    profile = client.verify_connection()
    print(f"  |-- Connected as Developer: '{profile['username']}' ({profile['email']})\n")
except Exception as err:
    print(f"  |-- Running in SDK Local Tracing Mode ({err})\n")

print("Step 2: Executing Agent Pipeline Evaluation...")
results = client.evaluate_agent(
    agent_fn=my_custom_agent,
    test_cases=test_cases,
    agent_name="Customer Service RAG Agent V1"
)

print("\n===============================================================")
print("[SUCCESS] Evaluation Complete!")
print(f"Total Test Cases Evaluated: {results['total_cases']}")
print(f"Average Pipeline Latency: {results['avg_latency_ms']} ms")
print(f"Granular Step Traces Recorded: {len(results['cases'][0]['steps'])} steps/case")
print(f"View Run Results on Dashboard UI: {results['dashboard_url']}")
print("===============================================================")
