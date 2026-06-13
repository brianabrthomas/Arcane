"""
Agent foundation.

Each agent is an autonomous economic actor: it performs one specialized legal-
research task and charges a fixed USDC price for it. Agents reason with Claude
when LLM_MODE=live; otherwise they fall back to deterministic, explainable
heuristics so the platform demos with zero credentials.

This is a purpose-built orchestration layer (no heavyweight framework needed),
but each agent's `think()` is a standard single-tool JSON call and drops cleanly
into LangChain/CrewAI if you'd rather host them there.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..config import get_settings

settings = get_settings()


@dataclass
class AgentResult:
    agent: str
    task: str
    output: dict
    confidence: float
    price_usdc: float


class Agent:
    name: str = "Agent"
    task: str = "task"
    price_usdc: float = 0.001          # what this agent charges per call
    system: str = "You are a precise legal-analytics agent."

    def think(self, prompt: str, schema_hint: str) -> dict:
        """Ask Claude for a structured JSON answer (live) or return {} (sim)."""
        if not settings.llm_live:
            return {}
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=settings.ANTHROPIC_MODEL,
                max_tokens=700,
                system=self.system + (
                    "\nReturn ONLY minified JSON, no prose, no markdown fences. "
                    "Schema: " + schema_hint
                ),
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
            return json.loads(text)
        except Exception:
            return {}

    def run(self, ctx: dict) -> AgentResult:  # pragma: no cover - overridden
        raise NotImplementedError
