from datetime import datetime, timedelta

WORK_START = 10
WORK_END = 19

SERVICE_DURATION: dict[str, int] = {
    "basic": 30,
    "full":  120,
}

_ALLOWED_SERVICE_TYPES = frozenset(SERVICE_DURATION.keys())


def validate_service_type(service_type: str) -> str:
    """
    Normalise and validate service_type.
    Raises ValueError for unknown values so callers fail fast.
    """
    normalised = service_type.strip().lower()
    if normalised not in _ALLOWED_SERVICE_TYPES:
        raise ValueError(
            f"Invalid service_type {service_type!r}. Must be one of {set(_ALLOWED_SERVICE_TYPES)}."
        )
    return normalised


def generate_slots(date: str, service_type: str):
    service_type = validate_service_type(service_type)
    duration = SERVICE_DURATION[service_type]

    start_time = datetime.strptime(f"{date} {WORK_START}:00", "%Y-%m-%d %H:%M")
    end_time   = datetime.strptime(f"{date} {WORK_END}:00",   "%Y-%m-%d %H:%M")

    slots = []
    while start_time + timedelta(minutes=duration) <= end_time:
        slots.append({"start": start_time, "end": start_time + timedelta(minutes=duration)})
        start_time += timedelta(minutes=30)

    return slots


def is_conflict(new_start, new_end, existing_start, existing_end) -> bool:
    return not (new_end <= existing_start or new_start >= existing_end)
