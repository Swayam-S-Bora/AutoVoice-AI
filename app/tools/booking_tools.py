from app.services.booking_service import get_available_slots, create_booking
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
