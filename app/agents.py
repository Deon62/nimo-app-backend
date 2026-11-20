"""Agent catalogue. app/data/agents.json is exported from the app (src/data/agents.ts) by
scripts/export_agents.mjs so names, prompts and voices stay in sync."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings

DATA = Path(__file__).parent / "data" / "agents.json"


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    role: str
    category: str
    tagline: str
    personality: str
    helps_with: str
    starters: list[str]
    instructions: str
    voice_id: str | None
    voice_sample: str

    @property
    def effective_voice_id(self) -> str:
        """Agents without a generated voice yet (Ryan) fall back to the configured default."""
        return self.voice_id or get_settings().elevenlabs_fallback_voice_id

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "category": self.category,
            "tagline": self.tagline,
            "helps_with": self.helps_with,
            "starters": self.starters,
            "has_voice": self.voice_id is not None,
        }


@lru_cache
def all_agents() -> dict[str, Agent]:
    rows = json.loads(DATA.read_text(encoding="utf-8"))
    return {row["id"]: Agent(**row) for row in rows}


def get_agent(agent_id: str) -> Agent:
    agent = all_agents().get(agent_id)
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent
