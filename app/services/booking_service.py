from app.db.supabase_client import supabase
from app.services.slot_service import generate_slots, is_conflict, SERVICE_DURATION
from app.core.logger import app_logger, error_logger
from datetime import datetime, timedelta


def get_available_slots(date, service_type):
    try:
        app_logger.info(f"Fetching slots | date={date}, service={service_type}")

        slots = generate_slots(date, service_type)

        bookings = supabase.table("appointments") \
            .select("*") \
            .eq("appointment_date", date) \
            .eq("status", "booked") \
            .execute().data

        available = []

        for slot in slots:
            conflict = False

            for b in bookings:
                existing_start = datetime.fromisoformat(f"{b['appointment_date']} {b['start_time']}")
                existing_end = datetime.fromisoformat(f"{b['appointment_date']} {b['end_time']}")

                if is_conflict(slot["start"], slot["end"], existing_start, existing_end):
                    conflict = True
                    break

            if not conflict:
                available.append({
                    "start_time": slot["start"].strftime("%H:%M"),
                    "end_time": slot["end"].strftime("%H:%M")
                })

        app_logger.info(f"Available slots count: {len(available)}")

        return available

    except Exception as e:
        error_logger.error(f"Slot Error: {str(e)}")
        return []


def create_booking(customer, date, start_time, service_type):
    try:
        app_logger.info(f"Creating booking | {customer} | {date} {start_time}")

        duration = SERVICE_DURATION[service_type.lower()]

        start_dt = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration)

        available = get_available_slots(date, service_type)

        if start_time not in [s["start_time"] for s in available]:
            app_logger.warning("Slot not available")
            return {"error": "Slot not available"}

        customer_res = supabase.table("customers").upsert(
            customer, on_conflict="phone"
        ).execute()
        customer_id = customer_res.data[0]["id"]

        booking = supabase.table("appointments").insert({
            "customer_id": customer_id,
            "service_type": service_type,
            "appointment_date": date,
            "start_time": start_time,
            "end_time": end_dt.strftime("%H:%M"),
            "status": "booked"
        }).execute()

        app_logger.info("Booking successful")

        return booking.data

    except Exception as e:
        error_logger.error(f"Booking Error: {str(e)}")
        return {"error": "Booking failed"}


def update_booking(phone: str, updates: dict):
    """
    Find the most recent 'booked' appointment for this phone and update it.
    `updates` may contain: date, start_time, service_type, name, car_model.
    - name / car_model are patched on the customers table.
    - date / start_time / service_type are patched on the appointments table.
    Recalculates end_time whenever start_time or service_type changes.
    Returns the updated appointment row or {"error": "..."}.
    """
    try:
        app_logger.info(f"Updating booking | phone=***{phone[-4:]} | updates={updates}")

        # Resolve the customer_id from phone
        cust_res = supabase.table("customers") \
            .select("id") \
            .eq("phone", phone) \
            .limit(1) \
            .execute()

        if not cust_res.data:
            return {"error": "No customer found for this phone number."}

        customer_id = cust_res.data[0]["id"]

        # --- Patch customer profile fields if supplied ---
        customer_patch = {}
        if "name" in updates and updates["name"]:
            customer_patch["name"] = updates["name"]
        if "car_model" in updates and updates["car_model"]:
            customer_patch["car_model"] = updates["car_model"]

        if customer_patch:
            supabase.table("customers") \
                .update(customer_patch) \
                .eq("id", customer_id) \
                .execute()
            app_logger.info(f"Customer profile updated: {customer_patch}")

        # --- Patch appointment fields if supplied ---
        appt_updates = {k: v for k, v in updates.items()
                        if k not in ("name", "car_model") and v}

        if not appt_updates:
            appt_res = supabase.table("appointments") \
                .select("*") \
                .eq("customer_id", customer_id) \
                .eq("status", "booked") \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()
            return appt_res.data[0] if appt_res.data else {"error": "No active booking found."}

        # Find the most recent booked appointment
        appt_res = supabase.table("appointments") \
            .select("*") \
            .eq("customer_id", customer_id) \
            .eq("status", "booked") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if not appt_res.data:
            return {"error": "No active booking found to update."}

        existing = appt_res.data[0]
        appt_id = existing["id"]

        patch = {}
        new_date = appt_updates.get("date") or existing["appointment_date"]
        new_start = appt_updates.get("start_time") or existing["start_time"]
        new_service = appt_updates.get("service_type") or existing["service_type"]

        if "date" in appt_updates:
            patch["appointment_date"] = new_date
        if "start_time" in appt_updates:
            patch["start_time"] = new_start
        if "service_type" in appt_updates:
            patch["service_type"] = new_service

        if not patch:
            return {"error": "No valid fields supplied for update."}

        new_start_time = new_start
        available = get_available_slots(new_date, new_service)
        available_times = [s["start_time"] for s in available]

        if (new_date == existing["appointment_date"]
                and new_start_time == existing["start_time"]
                and new_service == existing["service_type"]):
            return existing

        if new_start_time not in available_times:
            return {
                "error": "Slot not available",
                "available_slots": available_times,
                "date": new_date,
            }

        duration = SERVICE_DURATION[new_service.lower()]
        start_dt = datetime.strptime(f"{new_date} {new_start_time}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=duration)
        patch["end_time"] = end_dt.strftime("%H:%M")

        result = supabase.table("appointments") \
            .update(patch) \
            .eq("id", appt_id) \
            .execute()

        app_logger.info(f"Booking updated: {patch}")
        return result.data[0] if result.data else {"error": "Update returned no data."}

    except Exception as e:
        error_logger.error(f"update_booking error: {str(e)}")
        return {"error": f"Update failed: {str(e)}"}


def cancel_booking(phone: str):
    """
    Find the most recent 'booked' appointment for this phone, delete it from the
    appointments table, and return the cancelled booking details (for the receipt).
    Returns the deleted appointment row dict or {"error": "..."}.
    """
    try:
        app_logger.info(f"Cancelling booking | phone=***{phone[-4:]}")

        # Resolve customer
        cust_res = supabase.table("customers") \
            .select("id, name, car_model") \
            .eq("phone", phone) \
            .limit(1) \
            .execute()

        if not cust_res.data:
            return {"error": "No customer found for this phone number."}

        customer = cust_res.data[0]
        customer_id = customer["id"]

        # Find most recent booked appointment
        appt_res = supabase.table("appointments") \
            .select("*") \
            .eq("customer_id", customer_id) \
            .eq("status", "booked") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if not appt_res.data:
            return {"error": "No active booking found to cancel."}

        appt = appt_res.data[0]
        appt_id = appt["id"]

        # Delete the appointment row
        supabase.table("appointments") \
            .delete() \
            .eq("id", appt_id) \
            .execute()

        app_logger.info(f"Booking deleted | appt_id={appt_id}")

        # Return the cancelled booking details for the receipt
        return {
            "cancelled": True,
            "name": customer.get("name"),
            "car_model": customer.get("car_model"),
            "service_type": appt.get("service_type"),
            "date": appt.get("appointment_date"),
            "time": appt.get("start_time"),
        }

    except Exception as e:
        error_logger.error(f"cancel_booking error: {str(e)}")
        return {"error": f"Cancellation failed: {str(e)}"}