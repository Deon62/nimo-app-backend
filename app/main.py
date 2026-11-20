from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.http import create_client
from app.routers import agents, billing, calls, webhooks


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

app.include_router(agents.router)
app.include_router(calls.router)
app.include_router(billing.router)
app.include_router(webhooks.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"ok": True}
