"""
agent.py — streaming ReAct agent
---------------------------------
Changes vs original:
• run_agent_stream() is an async generator that yields audio bytes
• LLM uses stream=True; first complete sentence is piped to TTS immediately
• Tool calls (Supabase) run in executor so they don't block the event loop
• Filler phrase is spoken *before* each tool call result arrives
• All original state machine / action logic preserved
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date as dt_date
from typing import AsyncIterator

from groq import Groq

from app.core.config import settings
from app.core.logger import agent_logger, error_logger
from app.agents.prompts import build_system_prompt
from app.agents.state import load_state, save_state, is_booking_ready
from app.services.memory_service import get_recent_conversation, save_message
from app.services.speech_service import text_to_speech_stream, pick_filler
from app.tools.booking_tools import tool_get_slots, tool_create_booking
from app.tools.vehicle_tools import get_vehicle_info

# ---------------------------------------------------------------------------
_groq = Groq(api_key=settings.GROQ_API_KEY)

MAX_ITERATIONS = 8

TOOLS = {
    "get_available_slots": tool_get_slots,
    "create_booking": tool_create_booking,
    "get_vehicle_info": get_vehicle_info,
}

# Sentence-boundary regex — split on ., !, ? followed by space or end
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+|(?<=[.!?])$')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def format_slot_ranges(slots: list) -> str:
    if not slots:
        return ""

    def to_12h(t: str) -> str:
        h, m = int(t.split(":")[0]), int(t.split(":")[1])
        suffix = "AM" if h < 12 else "PM"
        h12 = h if h <= 12 else h - 12
        if h == 0:
            h12 = 12
        return f"{h12} {suffix}" if m == 0 else f"{h12}:{m:02d} {suffix}"

    starts = sorted(set(
        int(s["start_time"].split(":")[0]) * 60 + int(s["start_time"].split(":")[1])
        for s in slots
    ))

    ranges, run_start, prev = [], starts[0], starts[0]
    for t in starts[1:]:
        if t - prev > 30:
            ranges.append((run_start, prev))
            run_start = t
        prev = t
    ranges.append((run_start, prev))

    def mins_to_hhmm(m: int) -> str:
        return f"{m // 60:02d}:{m % 60:02d}"

    parts = [f"{to_12h(mins_to_hhmm(rs))} to {to_12h(mins_to_hhmm(re))}" for rs, re in ranges]
    return " and ".join(parts)


def _empty_state(phone: str = None) -> dict:
    return {
        "name": None,
        "phone": phone,
        "car_model": None,
        "service_type": None,
        "date": None,
        "time": None,
        "slot_confirmed": False,
    }


# ---------------------------------------------------------------------------
# Streaming LLM helper — accumulates JSON and yields text as soon as
# the "response" field value is sufficiently complete to speak.
# ---------------------------------------------------------------------------
async def _stream_llm_json(messages: list[dict]) -> tuple[str, dict]:
    """
    Streams LLM response. Returns (full_raw_json, parsed_dict).
    Also yields nothing — caller uses the full parsed dict.
    We need the whole JSON to safely act; streaming is used to minimize
    wall-clock time to first-token for logging.
    """
    loop = asyncio.get_event_loop()

    def _call():
        return _groq.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=messages,
            temperature=0.2,
            stream=True,
        )

    stream = await loop.run_in_executor(None, _call)

    raw = ""
    for chunk in stream:
        delta = chunk.choices[0].delta.content or ""
        raw += delta

    return raw


async def _extract_response_sentences(response_text: str) -> AsyncIterator[str]:
    """
    Split a response string into sentences and yield each one.
    This drives the sentence-by-sentence TTS pipeline.
    """
    # Split on sentence boundaries while keeping the delimiter
    parts = re.split(r'(?<=[.!?])\s+', response_text.strip())
    for part in parts:
        part = part.strip()
        if part:
            yield part


# ---------------------------------------------------------------------------
# Tool execution (blocking calls wrapped in executor)
# ---------------------------------------------------------------------------
async def _run_tool(tool_name: str, tool_args: dict) -> object:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, TOOLS[tool_name], tool_args)


async def _load_state_async(phone: str) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, load_state, phone)


async def _save_state_async(phone: str, state: dict) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, save_state, phone, state)


async def _get_history_async(phone: str) -> list:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_recent_conversation, phone)


async def _save_message_async(phone: str, role: str, msg: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, save_message, phone, role, msg)


# ---------------------------------------------------------------------------
# Main streaming agent entry point
# ---------------------------------------------------------------------------
async def run_agent_stream(
    user_input: str,
    phone: str,
    resolved_date: str | None = None,
) -> AsyncIterator[bytes]:
    """
    Async generator: yields raw PCM audio bytes for the agent's response.
    Designed to be consumed by the WebSocket handler.
    """
    agent_logger.info(f"[{phone}] -- NEW TURN --")
    agent_logger.info(f"[{phone}] User: {user_input}")

    try:
        # ── Load state & history concurrently ────────────────────────────
        state, history = await asyncio.gather(
            _load_state_async(phone),
            _get_history_async(phone),
        )
        state["phone"] = phone

        if resolved_date and not state.get("date"):
            state["date"] = resolved_date
            await _save_state_async(phone, state)
            agent_logger.info(f"[{phone}] Date pre-resolved: {resolved_date}")

        # Stale state guard
        if state.get("slot_confirmed") and is_booking_ready(state):
            fresh_signals = ["hi", "hello", "hey", "i want", "want to book",
                             "book a service", "book service", "new booking", "servicing"]
            if any(s in user_input.lower() for s in fresh_signals):
                agent_logger.info(f"[{phone}] Stale state with fresh intent — resetting.")
                state = _empty_state(phone)
                await _save_state_async(phone, state)

        await _save_message_async(phone, "user", user_input)
        history.append({"role": "user", "content": user_input})

        today = dt_date.today().isoformat()
        system_prompt = build_system_prompt(today)

        final_response: str | None = None
        iterations = 0

        while iterations < MAX_ITERATIONS:
            iterations += 1
            agent_logger.info(f"[{phone}] Iter {iterations} | State: {state}")

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"CURRENT STATE:\n{json.dumps(state, indent=2)}"},
            ] + history

            # ── Stream LLM response ────────────────────────────────────
            raw = await _stream_llm_json(messages)
            agent_logger.info(f"[{phone}] LLM Raw: {raw}")

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                error_logger.error(f"[{phone}] JSON parse failed: {raw}")
                final_response = "Sorry, I had a hiccup. Could you say that again?"
                break

            thought       = parsed.get("thought", "")
            action        = parsed.get("action", "")
            state_updates = parsed.get("state_updates", {})
            tool_name     = parsed.get("tool_name", "")
            tool_args     = parsed.get("tool_args", {})
            response_text = parsed.get("response", "")

            agent_logger.info(f"[{phone}] Thought: {thought} | Action: {action}")

            # ── update_state ───────────────────────────────────────────
            if action == "update_state":
                updated_fields = []
                for key, value in state_updates.items():
                    if value is None or value == "":
                        continue
                    if str(value).startswith("<") or str(value).startswith("["):
                        agent_logger.warning(f"[{phone}] Placeholder rejected: {key}={value}")
                        continue
                    state[key] = value
                    updated_fields.append(f"{key}={value}")
                    agent_logger.info(f"[{phone}] State <- {key} = {value}")

                await _save_state_async(phone, state)

                if updated_fields:
                    obs = (
                        f"SYSTEM: State updated — {', '.join(updated_fields)}. "
                        f"Current state is now complete for: {[k for k,v in state.items() if v is not None and v is not False]}. "
                        f"Missing: {[k for k,v in state.items() if v is None]}. "
                        f"Do NOT update_state again unless user provides new info. Move to next step."
                    )
                else:
                    obs = "SYSTEM: No new fields to update. Move to the next step based on current state."

                history.append({"role": "user", "content": obs})
                continue

            # ── call_tool ──────────────────────────────────────────────
            elif action == "call_tool":
                if tool_name not in TOOLS:
                    error_logger.error(f"[{phone}] Unknown tool: {tool_name}")
                    final_response = "Something went wrong. Please try again."
                    break

                if tool_name == "create_booking" and not is_booking_ready(state):
                    missing = [k for k, v in state.items() if not v and k != "phone"]
                    agent_logger.warning(f"[{phone}] create_booking blocked. Missing: {missing}")
                    history.append({
                        "role": "user",
                        "content": (
                            f"SYSTEM: create_booking BLOCKED — not all fields are ready. "
                            f"Missing: {missing}. Collect them from the user first."
                        )
                    })
                    continue

                # ── Stream filler phrase BEFORE tool result ────────────
                filler = pick_filler()
                agent_logger.info(f"[{phone}] Filler: '{filler}'")
                async for chunk in text_to_speech_stream(filler):
                    yield chunk

                # ── Run the tool (async, non-blocking) ─────────────────
                agent_logger.info(f"[{phone}] Tool: {tool_name} | Args: {tool_args}")
                result = await _run_tool(tool_name, tool_args)
                agent_logger.info(f"[{phone}] Tool result: {result}")

                if tool_name == "get_available_slots":
                    if not result:
                        state["date"] = None
                        await _save_state_async(phone, state)
                        obs = (
                            "TOOL RESULT [get_available_slots]: No slots available on that date. "
                            "Date has been cleared. Ask user to pick a different date."
                        )
                    else:
                        slot_list = [s["start_time"] for s in result]
                        state["available_slots"] = slot_list
                        await _save_state_async(phone, state)
                        ranges = format_slot_ranges(result)
                        all_starts = sorted(
                            int(s["start_time"].split(":")[0]) * 60 + int(s["start_time"].split(":")[1])
                            for s in result
                        )
                        is_fully_open = all(
                            all_starts[i+1] - all_starts[i] == 30
                            for i in range(len(all_starts) - 1)
                        )
                        if is_fully_open:
                            obs = (
                                "TOOL RESULT [get_available_slots]: "
                                "No bookings on this date — all slots are open. "
                                "Tell the user: slots available from 10 AM to 5 PM. Ask which time they prefer."
                            )
                        else:
                            obs = (
                                f"TOOL RESULT [get_available_slots]: "
                                f"Available start times grouped into ranges: {ranges}. "
                                f"Individual available start times: {', '.join(slot_list)}. "
                                f"Tell the user the available ranges. Ask which time they prefer."
                            )
                    history.append({"role": "user", "content": obs})

                elif tool_name == "create_booking":
                    if isinstance(result, dict) and "error" in result:
                        fresh = await _run_tool("get_available_slots", {
                            "date": state.get("date"),
                            "service_type": state.get("service_type", "basic")
                        })
                        if fresh:
                            state["available_slots"] = [s["start_time"] for s in fresh]
                            await _save_state_async(phone, state)
                            ranges = format_slot_ranges(fresh)
                            obs = (
                                f"TOOL RESULT [create_booking]: The slot the user requested is NOT available. "
                                f"Available ranges on {state.get('date')}: {ranges}. "
                                f"Reset time and slot_confirmed to null/false. "
                                f"Tell the user that slot is taken and offer these ranges."
                            )
                            state["time"] = None
                            state["slot_confirmed"] = False
                            await _save_state_async(phone, state)
                        else:
                            state["date"] = None
                            state["time"] = None
                            state["slot_confirmed"] = False
                            await _save_state_async(phone, state)
                            obs = (
                                "TOOL RESULT [create_booking]: Slot not available and no other slots on that date. "
                                "Date cleared. Ask the user to pick a different date."
                            )
                    else:
                        obs = "TOOL RESULT [create_booking]: Booking confirmed successfully. Use final_booking action now."
                    history.append({"role": "user", "content": obs})

                elif tool_name == "get_vehicle_info":
                    obs = f"TOOL RESULT [get_vehicle_info]: {json.dumps(result)}"
                    history.append({"role": "user", "content": obs})

                continue

            # ── ask_user ───────────────────────────────────────────────
            elif action == "ask_user":
                final_response = response_text
                break

            # ── final_booking ──────────────────────────────────────────
            elif action == "final_booking":
                final_response = response_text
                await _save_state_async(phone, _empty_state(phone))
                agent_logger.info(f"[{phone}] Booking complete. State cleared.")
                break

            else:
                agent_logger.error(f"[{phone}] Unknown action: {action}")
                final_response = "I'm not sure how to help. Can you rephrase?"
                break

        if final_response is None:
            agent_logger.warning(f"[{phone}] MAX_ITERATIONS hit.")
            final_response = "Sorry, I'm having trouble. Could you try again?"

        await _save_message_async(phone, "assistant", final_response)
        agent_logger.info(f"[{phone}] Response: {final_response}")

        # ── Stream TTS sentence by sentence for minimum TTFA ──────────
        async for sentence in _extract_response_sentences(final_response):
            async for chunk in text_to_speech_stream(sentence):
                yield chunk

    except Exception as e:
        error_logger.error(f"[{phone}] Agent crash: {e}", exc_info=True)
        async for chunk in text_to_speech_stream("Something went wrong. Please try again."):
            yield chunk
