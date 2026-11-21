import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.http import create_client
from app.routers import account, agents, billing, calls, hooks, webhooks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nimo")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = create_client()
    yield
    await app.state.http.aclose()


settings = get_settings()
app = FastAPI(
    title="Nimo API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.middleware("http")
async def log_requests(request: Request, call_next):
    """One line per request (method, path, status, time) so failures show up in the server logs."""
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("%s %s crashed", request.method, request.url.path)
        raise
    ms = (time.perf_counter() - started) * 1000
    level = logging.WARNING if response.status_code >= 400 else logging.INFO
    log.log(level, "%s %s -> %d (%.0f ms)", request.method, request.url.path, response.status_code, ms)
    return response


app.include_router(agents.router)
app.include_router(calls.router)
app.include_router(billing.router)
app.include_router(webhooks.router)
app.include_router(account.router)
app.include_router(hooks.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"ok": True}
