"""FastAPI webhook server.

GitHub sends a POST request here whenever a pull request is opened or
updated. We verify it, reply immediately with 202, and do the slow work
(GitHub API + LLM calls) in a background task, because GitHub marks a
delivery as failed if the response takes longer than about 10 seconds.
"""

import json
import logging

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from nitpick import jobs
from nitpick.config import Settings
from nitpick.security import verify_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

HANDLED_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}

app = FastAPI(title="nitpick")


def get_settings() -> Settings:
    return Settings.from_env()


def run_job_safely(job: jobs.ReviewJob, settings: Settings) -> None:
    try:
        jobs.run_review_job(job, settings)
    except Exception:
        logger.exception("Review failed for %s/%s#%d", job.owner, job.repo, job.number)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    # Signature must be checked against the raw bytes, before JSON parsing.
    body = await request.body()
    if not verify_signature(settings.webhook_secret, body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event")
    if event == "ping":
        return {"status": "pong"}
    if event != "pull_request":
        return {"status": "ignored", "reason": f"event {event}"}

    payload = json.loads(body)
    action = payload.get("action")
    pr = payload.get("pull_request") or {}

    if action not in HANDLED_ACTIONS:
        return {"status": "ignored", "reason": f"action {action}"}
    if pr.get("draft"):
        return {"status": "ignored", "reason": "draft pull request"}

    repo = payload["repository"]
    job = jobs.ReviewJob(
        owner=repo["owner"]["login"],
        repo=repo["name"],
        number=pr["number"],
        head_sha=pr["head"]["sha"],
        installation_id=(payload.get("installation") or {}).get("id"),
    )
    background_tasks.add_task(run_job_safely, job, settings)
    return JSONResponse(status_code=202, content={"status": "queued"})
