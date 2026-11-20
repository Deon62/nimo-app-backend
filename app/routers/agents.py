import httpx
from fastapi import APIRouter, Depends, HTTPException, Response

from app.agents import all_agents, get_agent
from app.config import Settings, get_settings
from app.http import get_http
from app.services import elevenlabs

router = APIRouter(prefix="/agents", tags=["agents"])

# Voice previews never change, so cache them in memory to save ElevenLabs credits.
_preview_cache: dict[str, bytes] = {}


@router.get("")
def list_agents() -> list[dict]:
    return [a.public() for a in all_agents().values()]


@router.get("/{agent_id}/preview", response_class=Response, responses={200: {"content": {"audio/mpeg": {}}}})
async def voice_preview(
    agent_id: str,
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> Response:
    agent = get_agent(agent_id)
    voice_id = agent.effective_voice_id
    audio = _preview_cache.get(voice_id + agent.id)
    if audio is None:
        audio = await elevenlabs.text_to_speech(http, settings, voice_id, agent.voice_sample)
        _preview_cache[voice_id + agent.id] = audio
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


#: Voice for the caller's side of sample calls (Jessica); Alex speaks it when the agent is Maya.
CALLER_VOICE = "g6xIsTj2HwM6VR4iXFCw"
CALLER_VOICE_ALT = "L0Dsvb3SLTyegXwtm47J"


@router.get("/{agent_id}/sample/{line}", response_class=Response, responses={200: {"content": {"audio/mpeg": {}}}})
async def sample_line(
    agent_id: str,
    line: int,
    http: httpx.AsyncClient = Depends(get_http),
    settings: Settings = Depends(get_settings),
) -> Response:
    """One line of the agent's example call, voiced by ElevenLabs. Lines alternate caller / agent."""
    agent = get_agent(agent_id)
    lines = agent.sample_lines()
    if not 0 <= line < len(lines):
        raise HTTPException(404, "No such line")
    speaker, text = lines[line]
    if speaker == "agent":
        voice = agent.effective_voice_id
    else:
        voice = CALLER_VOICE if agent.voice_id != CALLER_VOICE else CALLER_VOICE_ALT
    key = f"sample:{agent.id}:{line}"
    audio = _preview_cache.get(key)
    if audio is None:
        audio = await elevenlabs.text_to_speech(http, settings, voice, text)
        _preview_cache[key] = audio
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})
