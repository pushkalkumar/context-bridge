import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from .memory import get_recent_checkpoints, init_db, save_checkpoint
from .models import Checkpoint, PlannerOutput
from .planner import run_planner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Context Bridge started — DB initialized at ~/.context-bridge/")
    yield


app = FastAPI(title="Context Bridge", lifespan=lifespan)


@app.post("/sync", response_model=PlannerOutput)
async def sync(checkpoint: Checkpoint):
    data = checkpoint.model_dump()

    if not data["timestamp"]:
        data["timestamp"] = datetime.utcnow().isoformat()

    if not data["project_id"]:
        data["project_id"] = str(uuid.uuid4())[:8]

    logger.info("project_id=%s current_task=%s", data["project_id"], data["current_task"])

    save_checkpoint(data)
    history = get_recent_checkpoints(data["project_id"], n=10)
    return run_planner(data, history)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "context-bridge"}


@app.get("/history/{project_id}")
async def history(project_id: str):
    return get_recent_checkpoints(project_id, n=20)
