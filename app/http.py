import httpx
from fastapi import Request

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def create_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT)


def get_http(request: Request) -> httpx.AsyncClient:
    """Shared connection pool created in the app lifespan."""
    return request.app.state.http
