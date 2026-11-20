"""Voice calls: start a call, send a turn (text or recorded audio), get the agent's spoken reply, end it."""

import base64
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.agents import get_agent
from app.auth import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.db import Supabase, eq, get_db
from app.http import get_http
from app.plans import get_plan
from app.services import billing, deepseek, elevenlabs

router = APIRouter(prefix="/calls", tags=["calls"])

HISTORY_TURNS = 20
MAX_AUDIO_BYTES = 10 * 1024 * 1024


class StartCall(BaseModel):
    agent_id: str


class TextTurn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    #: Return the reply as audio too. Set false for captions-only clients.
    speak: bool = True


class EndCall(BaseModel):
    duration_seconds: int = Field(ge=0, le=6 * 3600)


async def _owned_call(db: Supabase, call_id: str, user: CurrentUser) -> dict:
    call = await db.select_one("calls", {"id": eq(call_id), "user_id": eq(user.id)})
    if call is None:
        raise HTTPException(404, "Call not found")
    if call.get("ended_at"):
        raise HTTPException(409, "Call has ended")
    return call


async def _minutes_used_today(db: Supabase, user_id: str) -> float:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    rows = await db.select(
        "calls", {"user_id": eq(user_id), "started_at": f"gte.{since}"}, columns="duration_seconds"
    )
    return sum(r.get("duration_seconds") or 0 for r in rows) / 60


async def _reply(
    db: Supabase, http: httpx.AsyncClient, settings: Settings, call: dict, user_text: str, speak: bool
) -> dict:
    agent = get_agent(call["agent_id"])
    history = await db.select(
        "call_messages", {"call_id": eq(call["id"])}, columns="role,content", order="id.desc", limit=str(HISTORY_TURNS)
    )
    messages = [{"role": "system", "content": agent.instructions}]
    messages += [{"role": m["role"], "content": m["content"]} for m in reversed(history)]
    messages.append({"role": "user", "content": user_text})

    reply = await deepseek.chat(http, settings, messages)
    await db.insert(
        "call_messages",
        [
            {"call_id": call["id"], "role": "user", "content": user_text},
            {"call_id": call["id"], "role": "assistant", "content": reply},
        ],
    )
    audio = await elevenlabs.text_to_speech(http, settings, agent.effective_voice_id, reply) if speak else None
    return {
        "transcript": user_text,
        "reply": reply,
        "audio_base64": base64.b64encode(audio).decode() if audio else None,
        "audio_mime": "audio/mpeg" if audio else None,
    }


@router.post("")
async def start_call(
    body: StartCall, user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)
) -> dict:
    agent = get_agent(body.agent_id)
    plan = get_plan(await billing.current_plan_id(db, user.id))
    if plan.daily_minutes is not None and await _minutes_used_today(db, user.id) >= plan.daily_minutes:
        raise HTTPException(402, f"Daily limit of {plan.daily_minutes} minutes reached")
    [call] = await db.insert("calls", {"user_id": user.id, "agent_id": agent.id})
    return {"call_id": call["id"], "agent": agent.public(), "can_record": plan.can_record}


@router.post("/{call_id}/text")
async def text_turn(
    call_id: str,
    body: TextTurn,
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> dict:
    call = await _owned_call(db, call_id, user)
    return await _reply(db, http, settings, call, body.text.strip(), body.speak)


@router.post("/{call_id}/voice")
async def voice_turn(
    call_id: str,
    audio: UploadFile = File(..., description="The caller's recorded speech (m4a, mp3, wav, webm)"),
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> dict:
    call = await _owned_call(db, call_id, user)
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Empty audio")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio too large")
    text = await elevenlabs.speech_to_text(
        http, settings, data, audio.filename or "speech.m4a", audio.content_type or "audio/mp4"
    )
    if not text:
        return {"transcript": "", "reply": None, "audio_base64": None, "audio_mime": None}
    return await _reply(db, http, settings, call, text, speak=True)


@router.post("/{call_id}/end")
async def end_call(
    call_id: str, body: EndCall, user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)
) -> dict:
    await _owned_call(db, call_id, user)
    await db.update(
        "calls",
        {"id": eq(call_id)},
        {"ended_at": datetime.now(timezone.utc).isoformat(), "duration_seconds": body.duration_seconds},
    )
    return {"ok": True}
