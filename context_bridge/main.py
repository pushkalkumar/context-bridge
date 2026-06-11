import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .memory import get_all_projects, get_recent_checkpoints, init_db, save_checkpoint
from .models import Checkpoint, PlannerOutput
from .planner import run_planner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

_DASHBOARD = (Path(__file__).parent / "dashboard.html").read_text()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Context Bridge started — DB at ~/.context-bridge/context_bridge.db")
    yield


app = FastAPI(title="Context Bridge", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    return HTMLResponse(_DASHBOARD)


@app.post("/sync", response_model=PlannerOutput)
async def sync(checkpoint: Checkpoint):
    data = checkpoint.model_dump()

    if not data["timestamp"]:
        data["timestamp"] = datetime.utcnow().isoformat()
    if not data["project_id"]:
        data["project_id"] = str(uuid.uuid4())[:8]

    logger.info("project_id=%s current_task=%s", data["project_id"], data["current_task"])

    # Get history BEFORE saving so the current checkpoint is not in its own context
    history = get_recent_checkpoints(data["project_id"], n=10)
    result = run_planner(data, history)

    # Persist planner output alongside the checkpoint so the dashboard can show it
    data["_planner_output"] = result.model_dump()
    save_checkpoint(data)

    return result


@app.get("/health")
async def health():
    return {"status": "ok", "service": "context-bridge"}


@app.get("/projects")
async def projects():
    return get_all_projects()


@app.get("/history/{project_id}")
async def history(project_id: str, limit: int = 50):
    return get_recent_checkpoints(project_id, n=min(limit, 100))
