from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uuid
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Storage for agent commands
pending_commands = {}
command_results = {}

# Health
@app.get("/")
@app.get("/health")
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "SLH API", "version": "2.0"}

# Agent endpoints
@app.post("/api/agent/register")
def register_agent(data: dict):
    agent_id = data.get("agent_id", "osif_pc")
    pending_commands[agent_id] = []
    command_results[agent_id] = []
    return {"status": "ok", "agent_id": agent_id}

@app.get("/api/agent/command/{agent_id}")
def get_command(agent_id: str):
    commands = pending_commands.get(agent_id, [])
    if commands:
        return commands.pop(0)
    return {"command": None}

@app.post("/api/agent/response")
def agent_response(data: dict):
    agent_id = data.get("agent_id", "osif_pc")
    cmd_id = data.get("command_id")
    output = data.get("output", "")
    if agent_id not in command_results:
        command_results[agent_id] = []
    command_results[agent_id].append({"id": cmd_id, "output": output, "timestamp": datetime.now().isoformat()})
    return {"status": "ok"}

@app.post("/api/agent/send")
def send_command(data: dict):
    agent_id = data.get("agent_id", "osif_pc")
    command = data.get("command", "")
    cmd_id = str(uuid.uuid4())[:8]
    if agent_id not in pending_commands:
        pending_commands[agent_id] = []
    pending_commands[agent_id].append({"id": cmd_id, "command": command})
    return {"status": "ok", "command_id": cmd_id}

@app.get("/api/agent/result/{agent_id}/{command_id}")
def get_command_result(agent_id: str, command_id: str):
    if agent_id not in command_results:
        return {"output": None}
    for res in command_results[agent_id]:
        if res["id"] == command_id:
            return {"output": res["output"]}
    return {"output": None}

@app.get("/api/test")
def test():
    return {"message": "Bot can reach API!"}
