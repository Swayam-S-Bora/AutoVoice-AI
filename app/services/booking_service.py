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

        customer_res = supabase.table("customers").insert(customer).execute()
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