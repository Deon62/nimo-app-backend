"""Voice calls: start a call, send a turn (text or recorded audio), get the agent's spoken reply, end it."""

import base64
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.agents import get_agent
from app.auth import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.db import Storage, Supabase, eq, get_db, get_storage
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


class UpdateCall(BaseModel):
    recorded: bool


RECORDINGS = "recordings"
AUDIO_EXT = {"audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
             "audio/mpeg": "mp3", "audio/webm": "webm", "audio/wav": "wav", "audio/x-wav": "wav",
             "audio/3gpp": "3gp"}


def _recording_prefix(call: dict) -> str:
    return f"{call['user_id']}/{call['id']}"


async def _store_segment(storage: Storage, call: dict, who: str, data: bytes, content_type: str) -> None:
    """Saves one side of a turn. Names sort by time, so playback order is the call order."""
    ext = AUDIO_EXT.get(content_type.split(";")[0], "bin")
    await storage.upload(RECORDINGS, f"{_recording_prefix(call)}/{int(time.time() * 1000)}-{who}.{ext}", data, content_type)


def _public_call(c: dict) -> dict:
    return {
        "id": c["id"],
        "agent_id": c["agent_id"],
        "started_at": c["started_at"],
        "duration_seconds": c.get("duration_seconds") or 0,
        "recorded": c.get("recorded", False),
        "status": "completed" if (c.get("duration_seconds") or 0) > 0 else "cancelled",
    }


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
    db: Supabase,
    http: httpx.AsyncClient,
    settings: Settings,
    call: dict,
    user_text: str,
    speak: bool,
    storage: Storage | None = None,
    user_audio: tuple[bytes, str] | None = None,
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
    if call.get("recorded") and storage is not None:
        if user_audio:
            await _store_segment(storage, call, "user", *user_audio)
        if audio:
            await _store_segment(storage, call, "agent", audio, "audio/mpeg")
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
    # The app plays the agent's voice preview as the greeting; keep it in the conversation.
    await db.insert("call_messages", {"call_id": call["id"], "role": "assistant", "content": agent.voice_sample})
    return {
        "call_id": call["id"],
        "agent": agent.public(),
        "can_record": plan.can_record,
        "greeting": agent.voice_sample,
        "minutes_left_today": None
        if plan.daily_minutes is None
        else max(0.0, plan.daily_minutes - await _minutes_used_today(db, user.id)),
    }


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
    storage: Storage = Depends(get_storage),
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
    content_type = audio.content_type or "audio/mp4"
    return await _reply(db, http, settings, call, text, True, storage, (data, content_type))


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


@router.get("")
async def list_calls(user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)) -> list[dict]:
    rows = await db.select("calls", {"user_id": eq(user.id), "ended_at": "not.is.null"}, order="started_at.desc", limit="200")
    return [_public_call(c) for c in rows]


@router.get("/{call_id}")
async def get_call(call_id: str, user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)) -> dict:
    call = await db.select_one("calls", {"id": eq(call_id), "user_id": eq(user.id)})
    if call is None:
        raise HTTPException(404, "Call not found")
    messages = await db.select("call_messages", {"call_id": eq(call_id)}, columns="role,content", order="id.asc")
    return {**_public_call(call), "messages": messages}


@router.patch("/{call_id}")
async def update_call(
    call_id: str, body: UpdateCall, user: CurrentUser = Depends(get_current_user), db: Supabase = Depends(get_db)
) -> dict:
    """Turns recording on or off for the rest of the call (paid plans)."""
    await _owned_call(db, call_id, user)
    if body.recorded and not get_plan(await billing.current_plan_id(db, user.id)).can_record:
        raise HTTPException(402, "Call recording is on paid plans")
    await db.update("calls", {"id": eq(call_id)}, {"recorded": body.recorded})
    return {"ok": True}


@router.get("/{call_id}/recording")
async def recording(
    call_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> dict:
    """Short-lived links to each recorded segment, in order. Play them back to back."""
    call = await db.select_one("calls", {"id": eq(call_id), "user_id": eq(user.id)})
    if call is None:
        raise HTTPException(404, "Call not found")
    paths = await storage.list(RECORDINGS, _recording_prefix(call))
    return {"segments": await storage.signed_urls(RECORDINGS, paths)}


@router.delete("/{call_id}")
async def delete_call(
    call_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Supabase = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> dict:
    call = await db.select_one("calls", {"id": eq(call_id), "user_id": eq(user.id)})
    if call is None:
        raise HTTPException(404, "Call not found")
    await storage.remove(RECORDINGS, await storage.list(RECORDINGS, _recording_prefix(call)))
    await db.delete("calls", {"id": eq(call_id)})
    return {"ok": True}
