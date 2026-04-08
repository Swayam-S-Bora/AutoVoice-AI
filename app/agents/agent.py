import json
from datetime import date as dt_date, datetime
from groq import Groq
from app.core.config import settings
from app.core.logger import agent_logger, error_logger
from app.agents.prompts import build_system_prompt
from app.agents.state import load_state, save_state, is_booking_ready
from app.services.memory_service import get_recent_conversation, save_message
from app.tools.booking_tools import tool_get_slots, tool_create_booking
from app.tools.vehicle_tools import get_vehicle_info

client = Groq(api_key=settings.GROQ_API_KEY)

MAX_ITERATIONS = 8

TOOLS = {
    "get_available_slots": tool_get_slots,
    "create_booking": tool_create_booking,
    "get_vehicle_info": get_vehicle_info,
}


def format_slot_ranges(slots: list) -> str:
    """
    Given a list of {'start_time': 'HH:MM', 'end_time': 'HH:MM'} dicts,
    group consecutive start times into human-readable ranges.
    e.g.  "10 AM to 5 PM"  or  "10 AM to 12 PM and 3 PM to 5 PM"
    """
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


def run_agent(user_input: str, phone: str, resolved_date: str | None = None) -> str:
    try:
        agent_logger.info(f"[{phone}] -- NEW TURN --")
        agent_logger.info(f"[{phone}] User: {user_input}")

        # State: load from DB, always inject phone from session
        state = load_state(phone)
        state["phone"] = phone

        # If Python already resolved a date (e.g. "tomorrow" → "2026-04-06"),
        # inject it directly into state — LLM never has to guess.
        if resolved_date and not state.get("date"):
            state["date"] = resolved_date
            save_state(phone, state)
            agent_logger.info(f"[{phone}] Date pre-resolved by dateparser: {resolved_date}")

        # Stale state guard: if state is fully complete (slot_confirmed=True)
        # but user starts fresh (new greeting or new booking intent), reset state
        # so we don't silently rebook with old data.
        if state.get("slot_confirmed") and is_booking_ready(state):
            fresh_signals = ["hi", "hello", "hey", "i want", "want to book",
                             "book a service", "book service", "new booking", "servicing"]
            if any(s in user_input.lower() for s in fresh_signals):
                agent_logger.info(f"[{phone}] Stale state with fresh intent — resetting.")
                state = _empty_state(phone)
                save_state(phone, state)

        history = get_recent_conversation(phone)
        save_message(phone, "user", user_input)
        history.append({"role": "user", "content": user_input})

        # Today's date injected once per turn — LLM uses this to resolve "tomorrow" etc.
        today = dt_date.today().isoformat()
        system_prompt = build_system_prompt(today)

        final_response = None
        iterations = 0
        state_updated_this_iter = False  # tracks if we just did an update_state

        while iterations < MAX_ITERATIONS:
            iterations += 1
            agent_logger.info(f"[{phone}] Iteration {iterations} | State: {state}")

            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"CURRENT STATE:\n{json.dumps(state, indent=2)}"
                },
            ] + history

            response = client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=messages,
                temperature=0.2,
            )

            raw = response.choices[0].message.content
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

            # -- update_state ──────────────────────────────────────────────
            if action == "update_state":
                updated_fields = []
                for key, value in state_updates.items():
                    if value is None or value == "":
                        continue
                    # Reject LLM placeholders like <x> or [unknown]
                    if str(value).startswith("<") or str(value).startswith("["):
                        agent_logger.warning(f"[{phone}] Placeholder rejected: {key}={value}")
                        continue
                    state[key] = value
                    updated_fields.append(f"{key}={value}")
                    agent_logger.info(f"[{phone}] State <- {key} = {value}")

                save_state(phone, state)

                # CRITICAL: tell LLM what was saved so it stops re-updating
                # and knows to move to the next step
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

            # -- call_tool ─────────────────────────────────────────────────
            elif action == "call_tool":
                if tool_name not in TOOLS:
                    error_logger.error(f"[{phone}] Unknown tool: {tool_name}")
                    final_response = "Something went wrong. Please try again."
                    break

                # Hard gate: block create_booking if state not ready
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

                agent_logger.info(f"[{phone}] Tool: {tool_name} | Args: {tool_args}")
                result = TOOLS[tool_name](tool_args)
                agent_logger.info(f"[{phone}] Tool result: {result}")

                if tool_name == "get_available_slots":
                    if not result:
                        state["date"] = None
                        save_state(phone, state)
                        obs = (
                            "TOOL RESULT [get_available_slots]: No slots available on that date. "
                            "Date has been cleared. Ask user to pick a different date."
                        )
                    else:
                        slot_list = [s["start_time"] for s in result]
                        state["available_slots"] = slot_list
                        save_state(phone, state)
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
                                f"TOOL RESULT [get_available_slots]: "
                                f"No bookings on this date — all slots are open. "
                                f"Tell the user: slots available from 10 AM to 5 PM. Ask which time they prefer."
                            )
                        else:
                            obs = (
                                f"TOOL RESULT [get_available_slots]: "
                                f"Available start times grouped into ranges: {ranges}. "
                                f"Individual available start times: {', '.join(slot_list)}. "
                                f"Tell the user the available ranges (e.g. '10 AM to 12 PM and 3 PM to 5 PM'). Ask which time they prefer."
                            )
                    history.append({"role": "user", "content": obs})

                elif tool_name == "create_booking":
                    if isinstance(result, dict) and "error" in result:
                        # Re-fetch available slots so we can show the user the correct ranges
                        from app.tools.booking_tools import tool_get_slots as _tgs
                        fresh = _tgs({"date": state.get("date"), "service_type": state.get("service_type", "basic")})
                        if fresh:
                            state["available_slots"] = [s["start_time"] for s in fresh]
                            save_state(phone, state)
                            ranges = format_slot_ranges(fresh)
                            obs = (
                                f"TOOL RESULT [create_booking]: The slot the user requested is NOT available (already booked). "
                                f"Available ranges on {state.get('date')}: {ranges}. "
                                f"Individual available starts: {', '.join(state['available_slots'])}. "
                                f"Reset time and slot_confirmed to null/false. "
                                f"Tell the user that slot is taken and offer these ranges. Ask which time works."
                            )
                            state["time"] = None
                            state["slot_confirmed"] = False
                            save_state(phone, state)
                        else:
                            state["date"] = None
                            state["time"] = None
                            state["slot_confirmed"] = False
                            save_state(phone, state)
                            obs = (
                                f"TOOL RESULT [create_booking]: Slot not available and no other slots on that date. "
                                f"Date cleared. Ask the user to pick a different date."
                            )
                    else:
                        obs = "TOOL RESULT [create_booking]: Booking confirmed successfully. Use final_booking action now."
                    history.append({"role": "user", "content": obs})

                elif tool_name == "get_vehicle_info":
                    obs = f"TOOL RESULT [get_vehicle_info]: {json.dumps(result)}"
                    history.append({"role": "user", "content": obs})

                continue

            # -- ask_user ──────────────────────────────────────────────────
            elif action == "ask_user":
                final_response = response_text
                break

            # -- final_booking ─────────────────────────────────────────────
            elif action == "final_booking":
                final_response = response_text
                save_state(phone, _empty_state(phone))
                agent_logger.info(f"[{phone}] Booking complete. State cleared.")
                break

            else:
                agent_logger.error(f"[{phone}] Unknown action: {action}")
                final_response = "I'm not sure how to help. Can you rephrase?"
                break

        if final_response is None:
            agent_logger.warning(f"[{phone}] MAX_ITERATIONS hit.")
            final_response = "Sorry, I'm having trouble. Could you try again?"

        save_message(phone, "assistant", final_response)
        agent_logger.info(f"[{phone}] Response: {final_response}")
        return final_response

    except Exception as e:
        error_logger.error(f"[{phone}] Agent crash: {str(e)}", exc_info=True)
        return "Something went wrong. Please try again."


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
