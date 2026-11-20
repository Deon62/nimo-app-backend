"""Tiny Supabase (PostgREST) client using the service-role key. Server-side only."""

from typing import Any

import httpx
from fastapi import Depends

from app.config import Settings, get_settings
from app.errors import NotConfigured, UpstreamError
from app.http import get_http


class Supabase:
    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise NotConfigured("Supabase")
        self._http = http
        self._base = f"{settings.supabase_url.rstrip('/')}/rest/v1"
        key = settings.supabase_service_role_key
        self._headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def _request(self, method: str, table: str, *, params=None, json=None, prefer: str | None = None) -> Any:
        headers = dict(self._headers)
        if prefer:
            headers["Prefer"] = prefer
        res = await self._http.request(method, f"{self._base}/{table}", params=params, json=json, headers=headers)
        if res.status_code >= 400:
            raise UpstreamError("Supabase", res.text[:300])
        return res.json() if res.content else None

    async def select(self, table: str, filters: dict[str, str], columns: str = "*", **params) -> list[dict]:
        return await self._request("GET", table, params={"select": columns, **filters, **params})

    async def select_one(self, table: str, filters: dict[str, str], columns: str = "*") -> dict | None:
        rows = await self.select(table, filters, columns, limit="1")
        return rows[0] if rows else None

    async def insert(self, table: str, row: dict | list[dict]) -> list[dict]:
        return await self._request("POST", table, json=row, prefer="return=representation")

    async def upsert(self, table: str, row: dict, on_conflict: str) -> list[dict]:
        return await self._request(
            "POST",
            table,
            params={"on_conflict": on_conflict},
            json=row,
            prefer="return=representation,resolution=merge-duplicates",
        )

    async def update(self, table: str, filters: dict[str, str], values: dict) -> list[dict]:
        return await self._request("PATCH", table, params=filters, json=values, prefer="return=representation")


def get_db(http: httpx.AsyncClient = Depends(get_http), settings: Settings = Depends(get_settings)) -> Supabase:
    return Supabase(http, settings)


def eq(value: str) -> str:
    return f"eq.{value}"
