import time
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.routers.auth import get_current_user_from_token
from app.models.user import User
from app.models.agent import Agent
from app.sdk.evalplatform import trace_step, TraceContext
from app.providers import ModelGateway

router = APIRouter()

class PlaygroundRequest(BaseModel):
    method: str = Field(description="Method: 'rest_api' or 'sdk_trace'")
    agent_id: Optional[int] = Field(default=None)
    prompt: str = Field(description="Prompt or query to send to agent")
    provider: Optional[str] = Field(default="gemini")
    model: Optional[str] = Field(default=None)

# Sample decorated functions for SDK Tracing Demonstration
@trace_step("intent_parser", step_type="nlu")
def parse_user_intent(query: str):
    time.sleep(0.05) # simulate parsing
    return {"intent": "query_knowledge_base", "confidence": 0.98, "raw_query": query}

@trace_step("document_retrieval", step_type="retrieval")
def retrieve_relevant_docs(intent_dict: dict):
    time.sleep(0.08) # simulate vector search
    return {
        "context": [
            "Doc 1: Antigravity Platform supports multi-provider failover.",
            "Doc 2: Model Gateway tracks latency, token usage, and accuracy."
        ],
        "sources": ["kb_section_12", "kb_section_45"]
    }

@trace_step("llm_generation", step_type="generation")
def generate_agent_response(prompt: str, context: list, provider: str = "gemini", model: str = None):
    gw = ModelGateway()
    enriched_prompt = f"Context: {' '.join(context)}\n\nUser Question: {prompt}"
    res = gw.generate(prompt=enriched_prompt, provider=provider, model=model)
    return {
        "output": res.response_text or f"Generated response using {res.provider} ({res.model})",
        "provider": res.provider,
        "model": res.model,
        "latency_ms": res.latency_ms,
        "tokens": (res.input_tokens or 0) + (res.output_tokens or 0)
    }

@router.post("/execute-playground")
def execute_playground_request(
    req: PlaygroundRequest,
    user: Optional[User] = Depends(get_current_user_from_token),
    db: Session = Depends(get_db)
):
    """
    Executes an agent request via REST API or Python SDK @trace_step tracing.
    Returns full output, execution timing, and granular step traces.
    """
    start_time = time.perf_counter()
    
    agent = None
    if req.agent_id:
        agent = db.query(Agent).filter(Agent.id == req.agent_id).first()

    target_provider = agent.provider if agent else (req.provider or "gemini")
    target_model = agent.model if agent else req.model
    system_prompt = agent.system_prompt if (agent and hasattr(agent, 'system_prompt') and agent.system_prompt) else "You are a helpful AI Agent evaluation assistant."

    try:
        if req.method == "sdk_trace":
            # Run step-by-step SDK traced execution pipeline
            TraceContext.clear()
            
            intent = parse_user_intent(req.prompt)
            docs = retrieve_relevant_docs(intent)
            gen_res = generate_agent_response(
                prompt=f"{system_prompt}\nUser Input: {req.prompt}",
                context=docs["context"],
                provider=target_provider,
                model=target_model
            )

            total_latency = round((time.perf_counter() - start_time) * 1000, 2)
            steps = TraceContext.get_current_trace().copy()

            return {
                "execution_method": "Python SDK (@trace_step Pipeline)",
                "status": "success",
                "total_latency_ms": total_latency,
                "output": gen_res["output"],
                "provider_used": gen_res["provider"],
                "model_used": gen_res["model"],
                "step_traces": steps,
                "api_key_authenticated": user.username if user else "Anonymous",
            }

        else: # REST API Endpoint method
            gw = ModelGateway()
            full_prompt = f"System: {system_prompt}\nUser: {req.prompt}"
            res = gw.generate(prompt=full_prompt, provider=target_provider, model=target_model)
            total_latency = round((time.perf_counter() - start_time) * 1000, 2)

            return {
                "execution_method": "REST API Endpoint (/gateway/generate)",
                "status": res.status,
                "total_latency_ms": total_latency,
                "output": res.response_text or f"REST API Gateway response generated ({res.status})",
                "provider_used": res.provider,
                "model_used": res.model,
                "fallback_used": res.fallback_used,
                "step_traces": [
                    {
                        "step_name": "gateway_http_post",
                        "step_type": "api_request",
                        "latency_ms": res.latency_ms,
                        "result_preview": res.response_text[:150] if res.response_text else ""
                    }
                ],
                "api_key_authenticated": user.username if user else "Anonymous",
            }
    except Exception as e:
        import traceback
        err_msg = f"Playground error: {str(e)}\n{traceback.format_exc()}"
        print(err_msg)
        raise HTTPException(status_code=400, detail=str(e))
