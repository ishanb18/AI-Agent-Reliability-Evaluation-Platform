# ⚡ Antigravity AI Agent Reliability & Evaluation Platform

An end-to-end, production-grade **AI Agent Reliability, Evaluation, and Telemetry Platform**. Designed for modern AI engineers to benchmark, test, instrument, and gate agentic workflows (RAG, Multi-Tool Function Calling, Reflection Reasoners) before production deployment.

---

## 🌟 Key Features

- **Provider-Agnostic Model Gateway**: Dynamic routing with automatic multi-provider failover across **Google Gemini**, **Groq**, and **Ollama**.
- **Python SDK (`@trace_step`)**: Lightweight instrumentation library (`evalplatform`) for step-by-step telemetry, latency tracking, context chunk logging, and tool execution tracing.
- **Interactive Web Dashboard**: Modern dark-mode React UI with live SSE telemetry streams, evaluation matrices, experiment tracking, and an interactive SDK Sandbox.
- **AI Test Case Generator**: Auto-generate edge cases, adversarial prompts, and security test cases (Prompt Injections, PII Leaks) powered by LLMs.
- **CI/CD Deployment Gate (`POST /evaluations/gate`)**: Gating API endpoint to evaluate test case correctness and P95 latency thresholds before promoting agents to production.
- **Unified Docker Packaging**: Multi-stage Docker deployment serving both FastAPI REST endpoints and the compiled React SPA inside a single container.

---

## 🚀 Quick Start & Installation

### 1. Prerequisites
- Python 3.11+
- Node.js 20+ (for frontend development)
- Docker & Docker Compose (optional for containerized deployment)

### 2. Local Setup
```bash
# Clone the repository
git clone https://github.com/ishanb18/AI-Agent-Reliability-Evaluation-Platform.git
cd AI-Agent-Reliability-Evaluation-Platform

# Set up Python virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY / GROQ_API_KEY
```

### 3. Run the Backend & Dashboard UI
```bash
# Start FastAPI application with built React SPA UI
python -m uvicorn app.main:app --port 8000 --reload
```
Open your browser and navigate to:
- 🌐 **Web UI Dashboard**: `http://localhost:8000/ui`
- 📚 **Interactive Swagger API Docs**: `http://localhost:8000/docs`

---

## 🐳 Docker Deployment (One-Command Launch)

Deploy PostgreSQL and the unified FastAPI + React Web UI together:

```bash
docker compose up --build -d
```
Access the containerized platform at `http://localhost:8000/ui`.

---

## 🛠️ Python SDK Usage Example

Install the SDK locally or import directly from `app.sdk.evalplatform`:

```python
from app.sdk.evalplatform import EvalClient, trace_step

# 1. Initialize Client with API Key
client = EvalClient(api_key="your_platform_api_key", base_url="http://localhost:8000")

# 2. Decorate pipeline steps with @trace_step for telemetry
@trace_step("vector_retrieval", step_type="retrieval")
def retrieve_docs(query: str):
    return {"context": ["Doc 1: Antigravity supports multi-provider failover."]}

@trace_step("llm_synthesis", step_type="generation")
def generate_response(query: str, docs: list):
    return f"Answer based on docs: {docs[0]}"

# 3. Define complete agent workflow pipeline
def my_rag_agent(user_input: str):
    retrieved = retrieve_docs(user_input)
    answer = generate_response(user_input, retrieved["context"])
    return {"output": answer}

# 4. Evaluate Agent against Benchmark Test Cases
test_cases = [
    {"input": "How does failover work?", "expected_answer": "multi-provider failover"}
]

summary = client.evaluate_agent(
    agent_fn=my_rag_agent,
    test_cases=test_cases,
    agent_name="Customer Service RAG Pipeline V1"
)

print(f"Avg Latency: {summary['avg_latency_ms']} ms")
```

---

## 🛡️ CI/CD Automated Deployment Gate

Integrate the `/evaluations/gate` endpoint into your GitHub Actions / GitLab CI pipeline:

```bash
curl -X POST http://localhost:8000/evaluations/gate \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": 1,
    "min_score": 0.80,
    "max_p95_latency_ms": 3000.0
  }'
```

**Response**:
- `HTTP 200 OK`: `{"verdict": "PASS", "deployable": true}` (Pipeline proceeds)
- `HTTP 422 Unprocessable Entity`: `{"verdict": "FAIL", "deployable": false}` (Build blocked)

---

## 📊 3-Agent Workflow Benchmark

Run the included benchmark script to evaluate 3 distinct agent architectures:

```bash
python benchmark_3_agents.py
```
This evaluates:
1. **Knowledge RAG Pipeline Agent** (Retrieval + Synthesis)
2. **Multi-Tool Function Calling Agent** (Calculator, SQL, Weather API)
3. **Multi-Step Reflection Reasoner Agent** (Planning $\rightarrow$ Execution $\rightarrow$ Critique $\rightarrow$ Synthesis)

Outputs a comprehensive report to `FINAL_EVALUATION_REPORT.md`.

---

## 📝 License
Distributed under the MIT License. See `LICENSE` for details.
