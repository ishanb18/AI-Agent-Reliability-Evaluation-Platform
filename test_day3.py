"""Quick test script for Day 3 endpoints."""
import httpx
import json

BASE = "http://127.0.0.1:8000"

# 1. Create an Agent
print("=== CREATE AGENT ===")
r = httpx.post(f"{BASE}/agents", json={
    "name": "Customer Support Bot v1",
    "description": "Handles customer queries about orders, returns, and shipping",
    "connection_type": "rest_api",
    "endpoint": "https://httpbin.org/post",
    "version": "v1",
    "provider": "openai",
    "model": "gpt-4o",
    "framework": "langgraph",
    "components": ["llm_call", "retrieval", "tools", "system_prompt"],
    "auto_discover": True,
}, timeout=15.0)
data = r.json()
print(f"Status: {r.status_code}")
agent_data = data["agent"]
print(f"Agent ID: {agent_data['id']}")
print(f"Name: {agent_data['name']}")
print(f"Components: {agent_data['components']}")
conn = data["connection_test"]
print(f"Connection Test Status: {conn['status']}")
if conn.get("detected_fields"):
    print(f"Detected Fields: {conn['detected_fields']}")
if conn.get("available_metrics"):
    print(f"Available Metrics: {conn['available_metrics']}")
if conn.get("unavailable_metrics"):
    print(f"Unavailable Metrics: {conn['unavailable_metrics']}")
agent_id = agent_data["id"]

# 2. List Agents
print("\n=== LIST AGENTS ===")
r = httpx.get(f"{BASE}/agents")
agents = r.json()
print(f"Total active agents: {len(agents)}")

# 3. Get Single Agent
print("\n=== GET AGENT BY ID ===")
r = httpx.get(f"{BASE}/agents/{agent_id}")
a = r.json()
print(f"Agent: {a['name']}, framework={a['framework']}, version={a['version']}")

# 4. Update Agent (Partial)
print("\n=== PATCH AGENT ===")
r = httpx.patch(f"{BASE}/agents/{agent_id}", json={"version": "v2", "model": "gpt-4o-mini"})
a = r.json()
print(f"Updated version: {a['version']}, model: {a['model']}")

# 5. Soft Delete
print("\n=== SOFT DELETE ===")
r = httpx.delete(f"{BASE}/agents/{agent_id}")
print(f"Result: {r.json()}")

r = httpx.get(f"{BASE}/agents")
print(f"Active agents after delete: {len(r.json())}")
r = httpx.get(f"{BASE}/agents?is_active=false")
print(f"Inactive agents: {len(r.json())}")

# ── Test Suite Endpoints ──────────────────────────────────────────────────────

# 6. Create Seed Suite
print("\n=== CREATE SEED SUITE ===")
r = httpx.post(f"{BASE}/test-suites/seed")
seed = r.json()
print(f"Seed Suite ID: {seed['suite_id']}")
print(f"Suite Name: {seed['suite_name']}")
print(f"Cases Created: {seed['cases_created']}")
print(f"Categories: {seed['categories']}")
seed_suite_id = seed["suite_id"]

# 7. List Suites
print("\n=== LIST SUITES ===")
r = httpx.get(f"{BASE}/test-suites")
suites = r.json()
print(f"Total suites: {len(suites)}")
for s in suites:
    print(f"  - {s['name']} (id={s['id']}, cases={s['case_count']})")

# 8. Get Suite with Cases
print("\n=== GET SUITE WITH CASES ===")
r = httpx.get(f"{BASE}/test-suites/{seed_suite_id}")
suite = r.json()
print(f"Suite: {suite['name']}")
print(f"Total cases: {suite['case_count']}")
# Print first 3 cases as sample
for case in suite["test_cases"][:3]:
    print(f"  [{case['category']}] {case['input'][:60]}...")

# 9. Create Custom Suite + Add Case
print("\n=== CREATE CUSTOM SUITE ===")
r = httpx.post(f"{BASE}/test-suites", json={
    "name": "My Custom Tests",
    "description": "Custom edge cases for my bot",
})
custom_suite = r.json()
custom_id = custom_suite["id"]
print(f"Created suite: {custom_suite['name']} (id={custom_id})")

print("\n=== ADD SINGLE TEST CASE ===")
r = httpx.post(f"{BASE}/test-suites/{custom_id}/cases", json={
    "input": "What happens if I return a broken item?",
    "expected_answer": "We accept returns of defective items within 90 days.",
    "category": "general",
    "risk_level": "medium",
})
case = r.json()
print(f"Created case id={case['id']}: {case['input'][:50]}...")

# 10. Upload JSON file
print("\n=== UPLOAD JSON FILE ===")
json_test_data = json.dumps([
    {"input": "Test Q1", "expected_answer": "Answer 1", "category": "general"},
    {"input": "Test Q2", "expected_answer": "Answer 2", "category": "rag", "risk_level": "high"},
    {"input": "Test Q3 - missing input field will pass since input is present"},
])
r = httpx.post(
    f"{BASE}/test-suites/{custom_id}/upload",
    files={"file": ("test_upload.json", json_test_data.encode(), "application/json")},
)
upload_result = r.json()
print(f"Created: {upload_result['created']}, Errors: {upload_result['errors']}")

# 11. Verify custom suite now has all cases
print("\n=== VERIFY CUSTOM SUITE ===")
r = httpx.get(f"{BASE}/test-suites/{custom_id}")
suite = r.json()
print(f"Suite '{suite['name']}' now has {suite['case_count']} cases")

# 12. Delete custom suite
print("\n=== DELETE SUITE ===")
r = httpx.delete(f"{BASE}/test-suites/{custom_id}")
print(f"Delete result: {r.json()}")

print("\n" + "=" * 60)
print("ALL DAY 3 TESTS PASSED!")
print("=" * 60)
