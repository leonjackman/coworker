from typing import Optional
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from coworker.agents import AgentMode, AgentRuntimeRegistry, Language
from coworker.config import load_settings

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
settings = load_settings()
agent_registry = AgentRuntimeRegistry(settings)

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    mode: AgentMode = "single"
    language: Language = "zh"

class ChatResponse(BaseModel):
    response: str
    session_id: str
    mode: AgentMode
    provider: str

class RuntimeConfigResponse(BaseModel):
    workspace: str
    default_mode: AgentMode
    agent_provider: str
    available_modes: list[AgentMode]

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "workspace": str(settings.workspace_dir),
        "agent_provider": settings.agent_provider,
        "available_modes": ["single"],
    }

@app.get("/config", response_model=RuntimeConfigResponse)
async def runtime_config():
    return RuntimeConfigResponse(
        workspace=str(settings.workspace_dir),
        default_mode="single",
        agent_provider=settings.agent_provider,
        available_modes=["single"],
    )

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    session_id = request.session_id or str(uuid4())
    try:
        runtime = agent_registry.get_runtime(request.mode)
        reply = runtime.run(request.message, session_id, request.language)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ChatResponse(
        response=reply.content,
        session_id=session_id,
        mode=reply.mode,
        provider=reply.provider,
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
