import json
import urllib.request

BASE = "http://192.168.1.100:8000/v1"
MODEL = "qwen3.6-35b"

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for files in the workspace.",
            "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a workspace command.",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}}, "required": ["command"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user a question.",
            "parameters": {"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "object"}}}, "required": ["question"]},
        },
    },
]

body = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": "You are Coworker, a local coding assistant. Reply in 中文. Use workspace tools only when they are needed and keep answers concise."},
        {"role": "user", "content": "帮我看看当前工作区里有哪些文件，然后读取 main.py 的前 20 行。"},
    ],
    "tools": TOOLS,
    "tool_choice": "auto",
    "stream": True,
}

request = urllib.request.Request(
    f"{BASE}/chat/completions",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer 1"},
    method="POST",
)

print("=== REQUEST BODY (first 800 chars) ===")
print(json.dumps(body, ensure_ascii=False)[:800])
print()
print("=== RAW SSE RESPONSE ===")
try:
    with urllib.request.urlopen(request, timeout=60) as resp:
        print("HTTP", resp.status, resp.getheaders()[:5])
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            print(line)
except Exception as exc:
    print("EXCEPTION:", type(exc).__name__, str(exc)[:800])
