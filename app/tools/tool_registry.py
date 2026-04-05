from app.tools.booking_tools import tool_get_slots, tool_create_booking
from app.tools.vehicle_tools import get_vehicle_info

TOOLS = {
    "get_available_slots": tool_get_slots,
    "create_booking": tool_create_booking,
    "get_vehicle_info": get_vehicle_info
}