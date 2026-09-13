"""The writing layer.

Quantify computes every number itself. This module only turns those numbers into
sentences an operator can act on. It never invents a figure and it never changes
one.

Two writers exist and they produce the same shape of output:

* `claude` uses the Anthropic API with a set of analyst skills loaded into the
  system prompt. It is used when a key and the `anthropic` package are present.
* `local` is a deterministic writer built from the same structured record. It
  runs with no network and no key, so the product is complete out of the box.

The recommended model is Claude Opus 5. It is the strongest available model at
reading a structured record, holding several competing drivers in mind, and
writing a short paragraph about them. Cost is a few cents per location
per day because the daily brief is written once and cached.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SKILL_DIR = Path(__file__).resolve().parent / "skills"
DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "medium"

# Which skills each task loads. Keeping this in one place makes it obvious what
# the model was told before it wrote anything.
TASK_SKILLS: dict[str, tuple[str, ...]] = {
    "day_narrative": ("statistical-rigour", "plain-language", "demand-analysis"),
    "item_composition": ("plain-language", "menu-composition"),
    "day_review": ("statistical-rigour", "plain-language", "forecast-review"),
}

_SKILL_CACHE: dict[str, str] = {}
_SKILL_LOCK = threading.Lock()
_INFLIGHT: dict[str, bool] = {}
_INFLIGHT_LOCK = threading.Lock()


def load_skill(name: str) -> str:
    with _SKILL_LOCK:
        if name not in _SKILL_CACHE:
            path = SKILL_DIR / f"{name}.md"
            _SKILL_CACHE[name] = path.read_text(encoding="utf-8") if path.is_file() else ""
        return _SKILL_CACHE[name]


def skills_for(task: str) -> list[str]:
    return list(TASK_SKILLS.get(task, ()))


def model_name() -> str:
    return os.getenv("QUANTIFY_AI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _effort() -> str:
    value = os.getenv("QUANTIFY_AI_EFFORT", DEFAULT_EFFORT).strip().lower()
    return value if value in {"low", "medium", "high", "xhigh", "max"} else DEFAULT_EFFORT


def _api_key() -> str:
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip()


def _sdk_installed() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def status() -> dict[str, Any]:
    """What the interface should say about the writing layer."""
    key = bool(_api_key())
    sdk = _sdk_installed()
    # Nothing here is for an operator's screen. What the interface needs is
    # the state; the install hint belongs in docs/LIVE_SETUP.md and the log.
    if key and sdk:
        state = "connected"
    elif key and not sdk:
        state = "needs_package"
    else:
        state = "local"
    return {
        "state": state,
        "detail": "",
        "model": model_name() if key else None,
        "effort": _effort(),
    }


def available() -> bool:
    return bool(_api_key()) and _sdk_installed()


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{model_name()}|{raw}".encode("utf-8")).hexdigest()


def read_cache(conn: sqlite3.Connection, task: str, subject: str, digest: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT result_json,writer,model,created_at FROM ai_generations WHERE task=? AND subject=? AND fingerprint=?",
        (task, subject, digest),
    ).fetchone()
    if row is None:
        return None
    try:
        result = json.loads(row["result_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    result["_writer"] = row["writer"]
    result["_model"] = row["model"]
    result["_written_at"] = row["created_at"]
    return result


def write_cache(conn: sqlite3.Connection, task: str, subject: str, digest: str, writer: str, result: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO ai_generations(task,subject,fingerprint,writer,model,result_json,created_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(task,subject) DO UPDATE SET
             fingerprint=excluded.fingerprint, writer=excluded.writer, model=excluded.model,
             result_json=excluded.result_json, created_at=excluded.created_at""",
        (
            task, subject, digest, writer, model_name() if writer == "claude" else "quantify-local",
            json.dumps(result, separators=(",", ":"), default=str),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------

def _system_blocks(task: str) -> list[dict[str, Any]]:
    """The stable prefix: the role, then every skill this task needs.

    This block is identical for every request of the same task, so it is marked
    for prompt caching and costs almost nothing after the first call of the day.
    """
    parts = [
        "You write the operating notes inside Quantify, a demand forecasting tool for "
        "restaurants, bakeries, and cafes.\n\n"
        "Quantify's own model produced every number you are given. You never recompute, "
        "adjust, or contradict a number. You explain what the numbers mean and how much "
        "to trust them.\n\n"
        "Your output is read by an owner or a general manager on a tablet in a kitchen, "
        "usually in under a minute, usually before service.\n\n"
        "Never mention Quantify, the model, or how a number was produced. Never answer an "
        "objection the reader did not make. You are writing about this restaurant, not "
        "about the software.\n\n"
        "The skills below define how you write. Follow them exactly."
    ]
    for name in skills_for(task):
        body = load_skill(name)
        if body:
            parts.append(body)
    text = "\n\n---\n\n".join(parts)
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _call_claude(task: str, payload: dict[str, Any], schema: dict[str, Any], max_tokens: int = 6000) -> dict[str, Any]:
    import anthropic

    client = anthropic.Anthropic(api_key=_api_key())
    user_text = (
        "Here is the structured record. Every figure in it is final.\n\n"
        f"```json\n{json.dumps(payload, indent=2, default=str)}\n```\n\n"
        "Write the response described by the output schema. Use the real counts from the "
        "record in your sentences, not just the percentages."
    )
    request = {
        "model": model_name(),
        "max_tokens": max_tokens,
        "system": _system_blocks(task),
        "messages": [{"role": "user", "content": user_text}],
        "output_config": {
            "effort": _effort(),
            "format": {"type": "json_schema", "schema": schema},
        },
    }
    try:
        response = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **request,
        )
    except (anthropic.BadRequestError, TypeError):
        # The fallback beta is not available on every account or SDK version.
        response = client.messages.create(**request)

    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("The writing model declined this request")
    text = next((block.text for block in response.content if block.type == "text"), "")
    if not text:
        raise RuntimeError("The writing model returned nothing")
    return json.loads(text)


def generate(
    conn: sqlite3.Connection,
    task: str,
    subject: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    local_writer: Callable[[dict[str, Any]], dict[str, Any]],
    force: bool = False,
) -> dict[str, Any]:
    """Return written output for one record, from cache when possible."""
    digest = fingerprint(payload)
    if not force:
        cached = read_cache(conn, task, subject, digest)
        if cached is not None:
            return cached

    writer = "local"
    result: dict[str, Any]
    if available():
        try:
            result = _call_claude(task, payload, schema)
            writer = "claude"
        except Exception as error:  # noqa: BLE001 - the local writer must always be able to take over
            result = local_writer(payload)
            result["_fallback_reason"] = str(error)[:280]
    else:
        result = local_writer(payload)

    write_cache(conn, task, subject, digest, writer, result)
    result["_writer"] = writer
    result["_model"] = model_name() if writer == "claude" else "quantify-local"
    result["_written_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return result


def claim_inflight(key: str) -> bool:
    """Stop two requests generating the same thing at once."""
    with _INFLIGHT_LOCK:
        if _INFLIGHT.get(key):
            return False
        _INFLIGHT[key] = True
        return True


def release_inflight(key: str) -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT.pop(key, None)


# ---------------------------------------------------------------------------
# Output schemas
# ---------------------------------------------------------------------------

CONFIDENCE_ENUM = {"type": "string", "enum": ["high", "medium", "low"]}

DAY_NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence, under 90 characters, naming what is different about today."},
        "summary": {"type": "string", "description": "Two or three sentences. Must contain at least one real comparison in units or money."},
        "confidence_note": {"type": "string", "description": "One sentence on what the confidence rests on."},
        "factors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string", "description": "Two or three words, for example Weather or Payday weekend."},
                    "explanation": {"type": "string", "description": "One or two sentences with the real counts."},
                    "confidence": CONFIDENCE_ENUM,
                    "based_on": {"type": "string", "description": "The evidence, for example 74 comparable Tuesdays."},
                },
                "required": ["heading", "explanation", "confidence", "based_on"],
                "additionalProperties": False,
            },
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "What to do, starting with a verb."},
                    "detail": {"type": "string", "description": "Why, in one sentence, with the number."},
                    "metric": {"type": "string", "description": "The short figure to show beside it."},
                },
                "required": ["title", "detail", "metric"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["headline", "summary", "confidence_note", "factors", "actions"],
    "additionalProperties": False,
}

COMPOSITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "One sentence saying what this item is."},
        "confidence": CONFIDENCE_ENUM,
        "verify_note": {"type": "string", "description": "What would make this certain."},
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["base", "protein", "dairy", "produce", "bread", "sauce", "sweetener", "beverage", "packaging", "other"],
                    },
                    "share": {"type": "integer", "description": "Rough share of the item by cost or bulk, 0 to 100."},
                    "quantity": {"type": "string", "description": "Amount per sold unit in kitchen units, or an empty string."},
                    "confidence": CONFIDENCE_ENUM,
                },
                "required": ["name", "role", "share", "quantity", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "confidence", "verify_note", "components"],
    "additionalProperties": False,
}

DAY_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence on how close the forecast was, in units and money."},
        "where_error_sat": {"type": "string", "description": "Which items or hours carried the error, with counts."},
        "likely_reason": {"type": "string", "description": "The cause if the data shows one, otherwise say it is unexplained."},
        "matters": {"type": "string", "description": "Whether the miss would have changed anything in the kitchen."},
    },
    "required": ["headline", "where_error_sat", "likely_reason", "matters"],
    "additionalProperties": False,
}
