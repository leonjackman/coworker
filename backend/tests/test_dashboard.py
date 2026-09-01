"""Tests for the project dashboard endpoint (``GET /projects/{id}/dashboard``).

Verifies the aggregate bundle shape: project meta, git status, agent roster
(with per-agent session counts), capabilities, the builtin tool catalog grouped
and filtered by agent mode, mcp servers, skills, and recent sessions. Also
covers error paths (unknown project, missing workspace).
"""

import os
import sys
from pathlib import Path

import pytest

BACKEND = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, BACKEND)

os.environ["COWORKER_DATA_DIR"] = str(Path(BACKEND) / ".test_dashboard_data")
os.environ["COWORKER_AGENT_PROVIDER"] = "simulated"
os.environ["COWORKER_LOG_LEVEL"] = "WARNING"

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "project-workspace"
    root.mkdir(parents=True, exist_ok=True)
    (root / "readme.md").write_text("# hello\n", encoding="utf-8")
    return root


def _create_project(client, name: str, workspace_path: str, mode: str = "single") -> str:
    resp = client.post(
        "/projects",
        json={"name": name, "workspace_path": workspace_path, "mode": mode},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["project"]["id"]


def test_dashboard_single_project_structure(client, workspace):
    project_id = _create_project(client, "single-proj", str(workspace))

    resp = client.get(f"/projects/{project_id}/dashboard")
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "ok"
    assert data["project"]["id"] == project_id
    assert data["project"]["mode"] == "single"
    assert data["project"]["workspace_path"] == str(workspace)

    # git: non-repo workspace reports no repository, never an error
    assert data["git"]["is_repo"] is False

    # single mode → exactly one default agent card
    assert len(data["agents"]) == 1
    agent = data["agents"][0]
    assert agent["id"] == "default_agent"
    assert agent["is_default"] is True
    assert agent["status"] == "active"

    # capabilities
    caps = data["capabilities"]
    assert caps["mode"] == "single"
    assert "memory_enabled" in caps
    assert "web_enabled" in caps
    assert "browser_enabled" in caps

    # tool catalog: grouped, no team tools in single mode, worker tools present
    builtin = data["tools"]["builtin"]
    names = [tool["name"] for tool in builtin]
    assert "run_command" in names
    assert "web_search" in names
    assert "use_worker" in names, "single mode keeps worker tools"
    assert "delegate_task" not in names, "team tools are excluded in single mode"
    for tool in builtin:
        assert tool["access"] in {"read", "write", "exec", "ask"}

    # mcp servers + skills are lists (may be empty in a fresh test env)
    assert isinstance(data["tools"]["mcp_servers"], list)
    assert isinstance(data["tools"]["skills"], list)
    assert isinstance(data["sessions"], list)


def test_dashboard_multi_project_includes_team_tools(client, workspace):
    project_id = _create_project(client, "multi-proj", str(workspace), mode="multi")

    resp = client.post(
        "/api/org/agent",
        json={"project_id": project_id, "name": "supply_agent", "role": "採購"},
    )
    assert resp.status_code == 200, resp.text

    resp = client.get(f"/projects/{project_id}/dashboard")
    assert resp.status_code == 200
    data = resp.json()

    assert data["project"]["mode"] == "multi"
    assert data["capabilities"]["mode"] == "multi"

    agents = data["agents"]
    assert len(agents) >= 2
    supply = next((a for a in agents if a["id"] == "supply_agent"), None)
    assert supply is not None
    assert supply["role"] == "採購"
    assert supply["is_default"] is False

    builtin = data["tools"]["builtin"]
    names = [tool["name"] for tool in builtin]
    assert "delegate_task" in names, "team tools are present in multi mode"
    assert "use_worker" not in names, "worker tools are excluded in multi mode"


def test_dashboard_session_counts_per_agent(client, workspace):
    project_id = _create_project(client, "session-proj", str(workspace), mode="multi")
    resp = client.post(
        "/api/org/agent",
        json={"project_id": project_id, "name": "supply_agent", "role": "採購"},
    )
    assert resp.status_code == 200, resp.text

    client.post("/sessions", json={"project_id": project_id, "title": "s1", "agent_id": "supply_agent"})
    client.post("/sessions", json={"project_id": project_id, "title": "s2", "agent_id": "supply_agent"})
    client.post("/sessions", json={"project_id": project_id, "title": "s3", "agent_id": "default_agent"})

    resp = client.get(f"/projects/{project_id}/dashboard")
    data = resp.json()
    counts = {agent["id"]: agent["session_count"] for agent in data["agents"]}
    assert counts.get("supply_agent") == 2
    assert counts.get("default_agent") == 1
    assert data["project"]["session_count"] == 3


def test_dashboard_unknown_project_returns_404(client):
    resp = client.get("/projects/does-not-exist/dashboard")
    assert resp.status_code == 404


def test_file_preview_kinds(client, workspace):
    """Dashboard file preview returns the right kind/payload per file type."""
    import base64

    (workspace / "notes.md").write_text("# hello\nworld\n", encoding="utf-8")
    (workspace / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00binary")
    (workspace / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
    (workspace / "sheet.xlsx").write_bytes(b"PK\x03\x04fake zip")
    (workspace / "deck.pptx").write_bytes(b"PK\x03\x04fake pptx")
    (workspace / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (workspace / "config.log").write_text("info: started\n", encoding="utf-8")

    project_id = _create_project(client, "preview-proj", str(workspace))

    def preview(path):
        resp = client.get(f"/workspace/file/preview?path={path}&project_id={project_id}")
        assert resp.status_code == 200, resp.text
        return resp.json()["preview"]

    text = preview("notes.md")
    assert text["kind"] == "text"
    assert "hello" in text["content"]

    img = preview("pic.png")
    assert img["kind"] == "image"
    assert img["data"] == base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00binary").decode("ascii")

    pdf = preview("doc.pdf")
    assert pdf["kind"] == "pdf"
    assert pdf["data"]

    # xlsx is renderable inline → base64 payload provided
    sheet = preview("sheet.xlsx")
    assert sheet["kind"] == "office"
    assert sheet["data"]

    # pptx is not inline-renderable → classified, no payload
    deck = preview("deck.pptx")
    assert deck["kind"] == "office"
    assert deck["previewable"] is False
    assert "data" not in deck

    # csv → table, log → text (engineering artifacts preview as text)
    assert preview("data.csv")["kind"] == "table"
    assert preview("config.log")["kind"] == "text"


def test_memory_discover_scoped_to_project(client, workspace):
    """Dashboard memory tab: ``scope=project`` must only surface the given
    project's memory, never other projects' memory."""
    dir_a = workspace / "a"
    dir_b = workspace / "b"
    dir_a.mkdir(parents=True, exist_ok=True)
    dir_b.mkdir(parents=True, exist_ok=True)
    project_a = _create_project(client, "memory-proj-a", str(dir_a))
    project_b = _create_project(client, "memory-proj-b", str(dir_b))

    # Resolve each project's memory_dir from the projects list.
    by_id = {p["id"]: p for p in client.get("/projects").json()["projects"]}
    memory_dir_a = by_id[project_a]["memory_dir"]

    # Unscoped discover shows every project.
    resp = client.get("/api/memory/discover")
    assert resp.status_code == 200
    all_names = {p["name"] for p in resp.json()["projects"]}
    assert len(all_names) >= 2

    # Scoped discover only surfaces project A's memory, system files excluded.
    resp = client.get(f"/api/memory/discover?project_id={project_a}&scope=project")
    assert resp.status_code == 200
    data = resp.json()
    assert data["system"] == []
    names = [p["name"] for p in data["projects"]]
    assert names == [memory_dir_a]

    # Unknown project in scoped mode → 404.
    resp = client.get("/api/memory/discover?project_id=does-not-exist&scope=project")
    assert resp.status_code == 404


def test_dashboard_deleted_workspace_degrades_gracefully(client, tmp_path):
    # A project whose workspace was deleted afterwards → still serves the
    # dashboard with git marked unavailable (never a hard error).
    root = tmp_path / "vanishing-workspace"
    root.mkdir(parents=True, exist_ok=True)
    project_id = _create_project(client, "vanishing-proj", str(root))
    import shutil

    shutil.rmtree(root)

    resp = client.get(f"/projects/{project_id}/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["project"]["id"] == project_id
    assert data["git"]["git"] is False
    assert data["git"]["note"] == "workspace unavailable"
