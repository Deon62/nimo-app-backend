"""Tiny Supabase (PostgREST) client using the service-role key. Server-side only."""

import logging
from typing import Any

import httpx
from fastapi import Depends

from app.config import Settings, get_settings
from app.errors import NotConfigured, UpstreamError
from app.http import get_http

log = logging.getLogger("nimo.supabase")


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
            # e.g. {"code":"42501","message":"permission denied for table calls"} — the role lacks a grant.
            log.error("%s %s -> %d: %s", method, table, res.status_code, res.text[:1000])
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

    async def delete(self, table: str, filters: dict[str, str]) -> None:
        await self._request("DELETE", table, params=filters)

    async def update(self, table: str, filters: dict[str, str], values: dict) -> list[dict]:
        return await self._request("PATCH", table, params=filters, json=values, prefer="return=representation")


def get_db(http: httpx.AsyncClient = Depends(get_http), settings: Settings = Depends(get_settings)) -> Supabase:
    return Supabase(http, settings)


def eq(value: str) -> str:
    return f"eq.{value}"


class Storage:
    """Supabase Storage via the REST API (service role)."""

    def __init__(self, http: httpx.AsyncClient, settings: Settings):
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise NotConfigured("Supabase")
        self._http = http
        self._root = settings.supabase_url.rstrip("/")
        self._base = f"{self._root}/storage/v1"
        key = settings.supabase_service_role_key
        self._headers = {"apikey": key, "Authorization": f"Bearer {key}"}

    async def upload(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        res = await self._http.post(
            f"{self._base}/object/{bucket}/{path}",
            content=data,
            headers={**self._headers, "Content-Type": content_type, "x-upsert": "true"},
        )
        if res.status_code >= 400:
            log.error("storage -> %d: %s", res.status_code, res.text[:1000])
            raise UpstreamError("Supabase Storage", res.text[:300])

    async def list_paths(self, bucket: str, prefix: str) -> list[str]:
        res = await self._http.post(
            f"{self._base}/object/list/{bucket}",
            json={"prefix": prefix, "limit": 1000, "sortBy": {"column": "name", "order": "asc"}},
            headers=self._headers,
        )
        if res.status_code >= 400:
            log.error("storage -> %d: %s", res.status_code, res.text[:1000])
            raise UpstreamError("Supabase Storage", res.text[:300])
        return [f"{prefix.rstrip('/')}/{o['name']}" for o in res.json() if o.get("id")]

    async def signed_urls(self, bucket: str, paths: list[str], expires_in: int = 3600) -> list[str]:
        if not paths:
            return []
        res = await self._http.post(
            f"{self._base}/object/sign/{bucket}",
            json={"expiresIn": expires_in, "paths": paths},
            headers=self._headers,
        )
        if res.status_code >= 400:
            log.error("storage -> %d: %s", res.status_code, res.text[:1000])
            raise UpstreamError("Supabase Storage", res.text[:300])
        return [f"{self._base}{item['signedURL']}" for item in res.json() if item.get("signedURL")]

    async def remove(self, bucket: str, paths: list[str]) -> None:
        if not paths:
            return
        res = await self._http.request(
            "DELETE", f"{self._base}/object/{bucket}", json={"prefixes": paths}, headers=self._headers
        )
        if res.status_code >= 400:
            log.error("storage -> %d: %s", res.status_code, res.text[:1000])
            raise UpstreamError("Supabase Storage", res.text[:300])

    async def delete_user(self, user_id: str) -> None:
        """Deletes the auth user; database rows cascade."""
        res = await self._http.delete(f"{self._root}/auth/v1/admin/users/{user_id}", headers=self._headers)
        if res.status_code >= 400 and res.status_code != 404:
            raise UpstreamError("Supabase Auth", res.text[:300])


def get_storage(http: httpx.AsyncClient = Depends(get_http), settings: Settings = Depends(get_settings)) -> Storage:
    return Storage(http, settings)
