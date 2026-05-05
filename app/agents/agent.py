"""
agent.py — streaming ReAct agent
---------------------------------
• run_agent_stream() is an async generator that yields audio bytes
• LLM uses stream=True; first complete sentence is piped to TTS immediately
• Tool calls (Supabase) run in executor so they don't block the event loop
• Filler phrase is spoken *before* each tool call result arrives
• LLM output validated with Pydantic before any action is taken
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date as dt_date
from typing import AsyncIterator

from groq import Groq
from pydantic import BaseModel, field_validator

from app.core.config import settings
from app.core.logger import agent_logger, error_logger
from app.agents.prompts import build_system_prompt
from app.agents.state import load_state, save_state, is_booking_ready, _empty_state
from app.services.memory_service import (
    get_recent_conversation, save_message,
    get_last_booking_summary, save_last_booking_summary,
)
from app.services.speech_service import (
    text_to_speech_stream, pick_filler, pick_farewell,
    pick_interruption, pick_greeting,
)
from app.tools.booking_tools import tool_get_slots, tool_create_booking, tool_update_booking

_groq = Groq(api_key=settings.GROQ_API_KEY)

MAX_ITERATIONS = 8

TOOLS = {
    "get_available_slots": tool_get_slots,
    "create_booking":      tool_create_booking,
    "update_booking":      tool_update_booking,
}

_ALLOWED_ACTIONS: set[str] = {"update_state", "call_tool", "ask_user", "final_booking"}

_GREETING_RE = re.compile(
    r"^(hi|hello|hey|good\s?(morning|afternoon|evening)|howdy|hiya|yo)[\s!?.]*$"
)

# Keywords that signal the caller wants to modify an existing booking
_MODIFY_RE = re.compile(
    r"\b(reschedule|rescheduling|change|update|modify|move|shift|postpone|"
    r"earlier|later|different\s+date|different\s+time|previous\s+booking|"
    r"existing\s+booking|my\s+booking)\b",
    re.IGNORECASE,
)


# In-memory intent cache: phone -> "new" | "modify"
# Guards against DB load failures causing intent to flip mid-session.
# Cleared when final_booking completes or the process restarts.
_intent_cache: dict = {}

# Pydantic schema
class AgentResponse(BaseModel):
    thought:       str = ""
    action:        str
    state_updates: dict = {}
    tool_name:     str = ""
    tool_args:     dict = {}
    response:      str = ""

    @field_validator("action")
    @classmethod
    def action_must_be_allowed(cls, v: str) -> str:
        if v not in _ALLOWED_ACTIONS:
            raise ValueError(f"Unknown action: {v!r}. Must be one of {_ALLOWED_ACTIONS}")
        return v

    @field_validator("tool_name")
    @classmethod
    def tool_name_must_be_known(cls, v: str) -> str:
        if v and v not in TOOLS:
            raise ValueError(f"Unknown tool: {v!r}. Must be one of {set(TOOLS)}")
        return v


# Slot formatting helper
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


# Streaming LLM helper
async def _stream_llm_json(messages: list[dict]) -> str:
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
    parts = re.split(r'(?<=[.!?])\s+', response_text.strip())
    for part in parts:
        part = part.strip()
        if part:
            yield part


# Async wrappers for blocking Supabase calls
async def _run_tool(tool_name: str, tool_args: dict) -> object:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, TOOLS[tool_name], tool_args)

async def _load_state_async(phone: str) -> dict:
    return await asyncio.get_event_loop().run_in_executor(None, load_state, phone)

async def _save_state_async(phone: str, state: dict) -> None:
    await asyncio.get_event_loop().run_in_executor(None, save_state, phone, state)

async def _get_history_async(phone: str) -> list:
    return await asyncio.get_event_loop().run_in_executor(None, get_recent_conversation, phone)

async def _save_message_async(phone: str, role: str, msg: str) -> None:
    await asyncio.get_event_loop().run_in_executor(None, save_message, phone, role, msg)

async def _get_last_booking_async(phone: str) -> dict | None:
    return await asyncio.get_event_loop().run_in_executor(None, get_last_booking_summary, phone)

async def _save_last_booking_async(phone: str, summary: dict) -> None:
    await asyncio.get_event_loop().run_in_executor(None, save_last_booking_summary, phone, summary)


# Main streaming agent entry point
async def run_agent_stream(
    user_input: str,
    phone: str,
    resolved_date: str | None = None,
    text_callback=None,          # async callable(str) — called with the final response text
) -> AsyncIterator[bytes]:
    """Async generator: yields raw PCM audio bytes for the agent's response.

    If *text_callback* is provided it is awaited with the agent's text reply
    just before audio synthesis begins, so the UI can show a caption without
    waiting for the full audio stream.
    """
    agent_logger.info(f"[***{phone[-4:]}] -- NEW TURN --")
    agent_logger.info(f"[***{phone[-4:]}] User: {user_input[:120]}")

    try:
        state, history, last_booking = await asyncio.gather(
            _load_state_async(phone),
            _get_history_async(phone),
            _get_last_booking_async(phone),
        )
        state["phone"] = phone

        #  FIX 1: Greeting fast-path
        _state_is_empty = not any(
            state.get(f) for f in ["name", "car_model", "service_type", "date", "time"]
        )
        if _state_is_empty and _GREETING_RE.match(user_input.strip().lower()):
            agent_logger.info(f"[***{phone[-4:]}] Greeting fast-path triggered.")
            greeting_text = pick_greeting()
            await _save_message_async(phone, "user", user_input)
            await _save_message_async(phone, "assistant", greeting_text)
            if text_callback:
                await text_callback(greeting_text)
            async for chunk in text_to_speech_stream(greeting_text):
                yield chunk
            return

        # ── Intent detection — set ONCE per session, never overwritten ──────────
        # Priority order:
        #   1. Already in DB-loaded state (normal case)
        #   2. In-memory cache (DB load failed but same process/session)
        #   3. Detect from current utterance (first relevant turn only)
        # "modify" is only set when the caller's FIRST non-greeting message
        # contains explicit modify keywords AND a previous booking exists.
        # All subsequent turns with "new" words must NOT re-evaluate intent.
        if state.get("booking_intent"):
            # State from DB already has intent — sync cache
            _intent_cache[phone] = state["booking_intent"]
        elif phone in _intent_cache:
            # DB load returned empty but we have it in memory — restore it
            state["booking_intent"] = _intent_cache[phone]
            agent_logger.info(
                f"[***{phone[-4:]}] Intent restored from cache: {_intent_cache[phone]}"
            )
            await _save_state_async(phone, state)
        else:
            # First relevant turn — detect from utterance
            if last_booking and _MODIFY_RE.search(user_input):
                state["booking_intent"] = "modify"
                agent_logger.info(f"[***{phone[-4:]}] Intent detected: modify")
            else:
                state["booking_intent"] = "new"
                agent_logger.info(f"[***{phone[-4:]}] Intent detected: new")
            _intent_cache[phone] = state["booking_intent"]
            await _save_state_async(phone, state)

        # ── FIX 7: Guard resolved_date writes when awaiting a time 
        # _awaiting_time_selection is true when slots have been shown but
        # slot_confirmed is still False. In that context any incoming
        # resolved_date (e.g. "5.30 p.m." -> today) must be ignored.
        _awaiting_time_selection = bool(
            state.get("available_slots") and not state.get("slot_confirmed")
        )

        if resolved_date and not state.get("date") and not _awaiting_time_selection:
            state["date"] = resolved_date
            await _save_state_async(phone, state)
            agent_logger.info(f"[***{phone[-4:]}] Date pre-resolved: {resolved_date}")
        elif resolved_date and _awaiting_time_selection:
            agent_logger.info(
                f"[***{phone[-4:]}] Date pre-resolve SKIPPED (awaiting time): {resolved_date}"
            )

        # FIX 2: Always inject previous booking context
        if last_booking:
            ctx = (
                f"SYSTEM: PREVIOUS_BOOKING on record for this caller - "
                f"Name: {last_booking.get('name')}, "
                f"Car: {last_booking.get('car_model')}, "
                f"Service: {last_booking.get('service_type')}, "
                f"Date: {last_booking.get('date')}, "
                f"Time: {last_booking.get('time')}. "
                f"booking_intent in state is '{state.get('booking_intent')}'. "
                f"If intent is 'modify': use update_booking (NOT create_booking). "
                f"If intent is 'new': ignore previous booking and collect fields fresh."
            )
            history.insert(0, {"role": "user", "content": ctx})
            agent_logger.info(f"[***{phone[-4:]}] Injected last booking context (intent={state.get('booking_intent')}).")

        # Stale state guard
        if state.get("slot_confirmed") and is_booking_ready(state):
            fresh_signals = [r"\bhi\b", r"\bhello\b", r"\bhey\b", r"\bnew booking\b",
                             r"\bwant to book\b", r"\bbook.*service\b", r"\bservicing\b"]
            if any(re.search(p, user_input.lower()) for p in fresh_signals):
                agent_logger.info(f"[***{phone[-4:]}] Stale state with fresh intent — resetting.")
                state = _empty_state(phone)
                await _save_state_async(phone, state)
                interruption_phrase = pick_interruption()
                async for chunk in text_to_speech_stream(interruption_phrase):
                    yield chunk

        await _save_message_async(phone, "user", user_input)
        history.append({"role": "user", "content": f"[CALLER]: {user_input}"})

        today = dt_date.today().isoformat()
        system_prompt = build_system_prompt(today)

        final_response: str | None = None
        iterations = 0

        while iterations < MAX_ITERATIONS:
            iterations += 1
            agent_logger.info(f"[***{phone[-4:]}] Iter {iterations} | State: {state}")

            # Inject per-iteration context hints
            context_hints = []
            if _awaiting_time_selection:
                context_hints.append(
                    "SYSTEM: You are awaiting a TIME SELECTION. "
                    "Interpret the caller's next utterance ONLY as a time of day. "
                    "Do NOT update name, car_model, service_type, or date. "
                    "If it cannot be mapped to a valid time, ask them to repeat."
                )
            # Use cache as fallback in case state.booking_intent was lost
            _effective_intent_hint = state.get("booking_intent") or _intent_cache.get(phone)
            if _effective_intent_hint == "modify":
                # Also correct state in-memory if it somehow lost the intent
                if state.get("booking_intent") != "modify":
                    state["booking_intent"] = "modify"
                    agent_logger.warning(f"[***{phone[-4:]}] Intent corrected to 'modify' from cache in iter {iterations}")
                context_hints.append(
                    "SYSTEM: booking_intent=modify. "
                    "You MUST use update_booking, NOT create_booking. "
                    "Do not collect name/car_model from scratch — use PREVIOUS_BOOKING values. "
                    "Only collect the fields the caller explicitly wants to change."
                )

            hint_block = ("\n\n" + "\n".join(context_hints)) if context_hints else ""

            messages = [
                {"role": "system", "content": system_prompt + hint_block},
                {"role": "user",   "content": f"CURRENT STATE:\n{json.dumps(state, indent=2)}"},
            ] + history

            raw = await _stream_llm_json(messages)
            agent_logger.info(f"[***{phone[-4:]}] LLM Raw (first 300): {raw[:300]}")

            try:
                parsed_data = json.loads(raw)
                parsed = AgentResponse(**parsed_data)
            except (json.JSONDecodeError, Exception) as exc:
                error_logger.error(f"[***{phone[-4:]}] LLM output invalid: {exc} | raw={raw[:200]}")
                final_response = "Sorry, could you say that again?"
                break

            thought       = parsed.thought
            action        = parsed.action
            state_updates = parsed.state_updates
            tool_name     = parsed.tool_name
            tool_args     = parsed.tool_args
            response_text = parsed.response

            agent_logger.info(f"[***{phone[-4:]}] Thought: {thought} | Action: {action}")

            # update_state 
            if action == "update_state":
                updated_fields = []
                _booking_fields = {"name", "car_model", "service_type", "date", "time", "slot_confirmed"}
                _any_booking_field_changed = False

                # ── Pre-pass: apply time + slot_confirmed first so field protection
                # lifts within the same update batch before other fields are processed.
                for key, value in state_updates.items():
                    if key in ("time", "slot_confirmed") and value not in (None, "", False):
                        if str(value).startswith("<") or str(value).startswith("["):
                            continue
                        state[key] = value
                        if key == "slot_confirmed" and value:
                            _awaiting_time_selection = False
                            agent_logger.info(f"[***{phone[-4:]}] _awaiting_time_selection lifted (pre-pass)")

                for key, value in state_updates.items():
                    if value is None or value == "":
                        continue
                    if str(value).startswith("<") or str(value).startswith("["):
                        agent_logger.warning(f"[***{phone[-4:]}] Placeholder rejected: {key}={value}")
                        continue
                    # Only protect name/car_model/service_type while still awaiting a time;
                    # the pre-pass above already lifted _awaiting_time_selection if time/slot arrived.
                    if _awaiting_time_selection and key in {"name", "car_model", "service_type"}:
                        agent_logger.warning(f"[***{phone[-4:]}] Field protection (time): refused {key}={value}")
                        continue

                    state[key] = value
                    updated_fields.append(f"{key}={value}")
                    agent_logger.info(f"[***{phone[-4:]}] State <- {key} = {value}")
                    if key == "slot_confirmed" and value:
                        _awaiting_time_selection = False
                    if key in _booking_fields and key != "slot_confirmed":
                        _any_booking_field_changed = True

                # For modify intent, auto-fill name/car/service from last_booking when still None
                if state.get("booking_intent") == "modify" and last_booking:
                    for field in ("name", "car_model", "service_type"):
                        if state.get(field) is None:
                            inherited = last_booking.get(field)
                            if inherited:
                                state[field] = inherited
                                updated_fields.append(f"{field}={inherited}(auto)")
                                agent_logger.info(f"[***{phone[-4:]}] Auto-filled {field}={inherited} from last_booking")

                # Reset booking_confirmed whenever a booking-relevant field changes
                if _any_booking_field_changed and state.get("booking_confirmed"):
                    state["booking_confirmed"] = False
                    updated_fields.append("booking_confirmed=false(reset)")
                    agent_logger.info(f"[***{phone[-4:]}] booking_confirmed reset due to field change")

                await _save_state_async(phone, state)

                if updated_fields:
                    obs = (
                        f"SYSTEM: State updated — {', '.join(updated_fields)}. "
                        f"Filled: {[k for k,v in state.items() if v is not None and v is not False]}. "
                        f"Missing: {[k for k,v in state.items() if v is None]}. "
                        f"booking_confirmed={state.get('booking_confirmed')}. "
                        f"Do NOT update_state again unless user provides new info. Move to next step."
                    )
                else:
                    obs = "SYSTEM: No new fields to update. Move to the next step based on current state."

                history.append({"role": "user", "content": obs})
                continue

            # call_tool
            elif action == "call_tool":
                if not tool_name:
                    error_logger.error(f"[***{phone[-4:]}] call_tool with empty tool_name")
                    final_response = "Something went wrong. Please try again."
                    break

                # Guard: modification intent must use update_booking, not create_booking
                # Check both state AND in-memory cache in case state was corrupted by a DB load failure
                _effective_intent = state.get("booking_intent") or _intent_cache.get(phone)
                if tool_name == "create_booking" and _effective_intent == "modify":
                    agent_logger.warning(f"[***{phone[-4:]}] create_booking blocked — intent is modify. Redirecting to update_booking.")
                    history.append({
                        "role": "user",
                        "content": (
                            "SYSTEM: create_booking is BLOCKED for modify intent. "
                            "You MUST call update_booking instead. "
                            "Use the phone from state and only the fields the caller wants changed."
                        )
                    })
                    continue

                # Guard: must have explicit caller confirmation before booking
                if tool_name in ("create_booking", "update_booking") and not state.get("booking_confirmed"):
                    agent_logger.warning(f"[***{phone[-4:]}] {tool_name} blocked — booking_confirmed is False.")
                    history.append({
                        "role": "user",
                        "content": (
                            f"SYSTEM: {tool_name} is BLOCKED — booking_confirmed is False. "
                            "You MUST read back all booking details to the caller and ask them "
                            "to confirm before proceeding. Use ask_user action with the full summary."
                        )
                    })
                    continue

                if tool_name == "create_booking" and not is_booking_ready(state):
                    missing = [k for k, v in state.items() if not v and k != "phone"]
                    agent_logger.warning(f"[***{phone[-4:]}] create_booking blocked. Missing: {missing}")
                    history.append({
                        "role": "user",
                        "content": (
                            f"SYSTEM: create_booking BLOCKED — not all fields are ready. "
                            f"Missing: {missing}. Collect them from the user first."
                        )
                    })
                    continue

                filler = pick_filler()
                agent_logger.info(f"[***{phone[-4:]}] Filler: '{filler}'")
                async for chunk in text_to_speech_stream(filler):
                    yield chunk

                agent_logger.info(f"[***{phone[-4:]}] Tool: {tool_name} | Args: {tool_args}")
                result = await _run_tool(tool_name, tool_args)
                agent_logger.info(f"[***{phone[-4:]}] Tool result: {str(result)[:200]}")

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
                        obs = (
                            f"TOOL RESULT [get_available_slots]: "
                            f"Available ranges: {ranges}. "
                            f"Individual start times: {', '.join(slot_list)}. "
                            f"Tell the user the ranges. Ask which time they prefer."
                        )
                        _awaiting_time_selection = True
                    history.append({"role": "user", "content": obs})

                elif tool_name == "create_booking":
                    if isinstance(result, dict) and "error" in result:
                        fresh = await _run_tool("get_available_slots", {
                            "date": state.get("date"),
                            "service_type": state.get("service_type", "basic")
                        })
                        if fresh:
                            state["available_slots"] = [s["start_time"] for s in fresh]
                            state["time"] = None
                            state["slot_confirmed"] = False
                            _awaiting_time_selection = True
                            await _save_state_async(phone, state)
                            ranges = format_slot_ranges(fresh)
                            obs = (
                                f"TOOL RESULT [create_booking]: Slot NOT available. "
                                f"Available on {state.get('date')}: {ranges}. "
                                f"Tell the user the slot is taken, offer these ranges."
                            )
                        else:
                            state["date"] = None
                            state["time"] = None
                            state["slot_confirmed"] = False
                            _awaiting_time_selection = False
                            await _save_state_async(phone, state)
                            obs = (
                                "TOOL RESULT [create_booking]: No slots on that date. "
                                "Date cleared. Ask the user to pick a different date."
                            )
                    else:
                        booking_summary = {
                            "name":         state.get("name"),
                            "car_model":    state.get("car_model"),
                            "service_type": state.get("service_type"),
                            "date":         state.get("date"),
                            "time":         state.get("time"),
                        }
                        await _save_last_booking_async(phone, booking_summary)
                        agent_logger.info(f"[***{phone[-4:]}] Last booking summary saved.")
                        obs = "TOOL RESULT [create_booking]: Booking confirmed. Use final_booking now."
                    history.append({"role": "user", "content": obs})

                elif tool_name == "update_booking":
                    if isinstance(result, dict) and "error" in result:
                        alt_slots = result.get("available_slots", [])
                        alt_date = result.get("date", state.get("date"))
                        if alt_slots:
                            ranges = format_slot_ranges(
                                [{"start_time": t} for t in alt_slots]
                            )
                            state["available_slots"] = alt_slots
                            _awaiting_time_selection = True
                            await _save_state_async(phone, state)
                            obs = (
                                f"TOOL RESULT [update_booking]: Slot not available. "
                                f"Available on {alt_date}: {ranges}. "
                                f"Ask the user to pick a different time."
                            )
                        else:
                            obs = f"TOOL RESULT [update_booking]: Update failed — {result['error']}. Let the user know."
                    else:
                        updated_appt = result if isinstance(result, dict) else {}
                        # Prefer state values for name/car_model (caller may have updated them)
                        booking_summary = {
                            "name":         state.get("name") or (last_booking.get("name") if last_booking else None),
                            "car_model":    state.get("car_model") or (last_booking.get("car_model") if last_booking else None),
                            "service_type": updated_appt.get("service_type") or state.get("service_type"),
                            "date":         updated_appt.get("appointment_date") or state.get("date"),
                            "time":         updated_appt.get("start_time") or state.get("time"),
                        }
                        await _save_last_booking_async(phone, booking_summary)
                        agent_logger.info(f"[***{phone[-4:]}] Last booking summary updated after modification.")
                        obs = "TOOL RESULT [update_booking]: Booking updated in DB. Use final_booking now."
                    history.append({"role": "user", "content": obs})

                continue

            # ask_user 
            elif action == "ask_user":
                final_response = response_text
                break

            # final_booking 
            elif action == "final_booking":
                final_response = response_text
                farewell = pick_farewell()
                if not final_response.rstrip().endswith(tuple(".!?")):
                    final_response = final_response.rstrip() + ". "
                else:
                    final_response = final_response.rstrip() + " "
                final_response += farewell

                # structured receipt data for frontend
                if text_callback:
                    receipt_payload = json.dumps({
                        "name":         state.get("name"),
                        "car_model":    state.get("car_model"),
                        "service_type": state.get("service_type"),
                        "date":         state.get("date"),   # YYYY-MM-DD
                        "time":         state.get("time"),   # HH:MM
                    }, ensure_ascii=False)
                    await text_callback(f"booking_confirmed:{receipt_payload}")

                await _save_state_async(phone, _empty_state(phone))
                _intent_cache.pop(phone, None)  # clear in-memory intent so next call starts fresh
                agent_logger.info(f"[***{phone[-4:]}] Booking complete. State + intent cache cleared.")
                break

        if final_response is None:
            agent_logger.warning(f"[***{phone[-4:]}] MAX_ITERATIONS hit.")
            final_response = "Sorry, I'm having trouble. Could you try again?"

        await _save_message_async(phone, "assistant", final_response)
        agent_logger.info(f"[***{phone[-4:]}] Response: {final_response[:120]}")

        if text_callback:
            await text_callback(final_response)

        async for sentence in _extract_response_sentences(final_response):
            async for chunk in text_to_speech_stream(sentence):
                yield chunk

    except Exception as e:
        error_logger.error(f"[***{phone[-4:]}] Agent crash: {e}", exc_info=True)
        async for chunk in text_to_speech_stream("Something went wrong. Please try again."):
            yield chunk