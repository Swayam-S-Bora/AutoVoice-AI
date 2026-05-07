from app.services.booking_service import get_available_slots, create_booking, update_booking, cancel_booking
from app.core.logger import log_action


def tool_get_slots(input_data: dict):
    """
    Args: { "date": "YYYY-MM-DD", "service_type": "basic|full" }
    """
    result = get_available_slots(input_data["date"], input_data["service_type"])
    log_action("get_available_slots", input_data, result)
    return result


def tool_create_booking(input_data: dict):
    """
    Args: {
        "customer": { "name": str, "phone": str, "car_model": str },
        "date": "YYYY-MM-DD",
        "start_time": "HH:MM",
        "service_type": "basic|full"
    }
    """
    customer = input_data.get("customer", {})
    date = input_data.get("date")
    start_time = input_data.get("start_time")
    service_type = input_data.get("service_type")

    result = create_booking(customer, date, start_time, service_type)
    log_action("create_booking", input_data, result)
    return result


def tool_update_booking(input_data: dict):
    """
    Updates an existing appointment for this phone number.
    Args: {
        "phone": str,
        "updates": {
            "date": "YYYY-MM-DD",          # optional
            "start_time": "HH:MM",         # optional
            "service_type": "basic|full",  # optional
            "name": str,                   # optional — updates customer profile
            "car_model": str,              # optional — updates customer profile
        }
    }
    Only the keys present in `updates` are changed.
    name/car_model patch the customers table; date/time/service patch the appointments table.
    Returns the updated appointment row or {"error": "..."}.
    """
    phone = input_data.get("phone")
    updates = input_data.get("updates", {})

    result = update_booking(phone, updates)
    log_action("update_booking", input_data, result)
    return result


def tool_cancel_booking(input_data: dict):
    """
    Cancels (deletes) the most recent booked appointment for this phone number.
    Args: { "phone": str }
    Returns the cancelled booking details or {"error": "..."}.
    """
    phone = input_data.get("phone")
    result = cancel_booking(phone)
    log_action("cancel_booking", input_data, result)
    return result