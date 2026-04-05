from datetime import datetime, timedelta

WORK_START = 10
WORK_END = 19

SERVICE_DURATION = {
    "basic": 30,
    "full": 120
}

def generate_slots(date: str, service_type: str):
    duration = SERVICE_DURATION[service_type.lower()]
    
    start_time = datetime.strptime(f"{date} {WORK_START}:00", "%Y-%m-%d %H:%M")
    end_time = datetime.strptime(f"{date} {WORK_END}:00", "%Y-%m-%d %H:%M")

    slots = []

    while start_time + timedelta(minutes=duration) <= end_time:
        slots.append({
            "start": start_time,
            "end": start_time + timedelta(minutes=duration)
        })
        start_time += timedelta(minutes=30)

    return slots


def is_conflict(new_start, new_end, existing_start, existing_end):
    return not (new_end <= existing_start or new_start >= existing_end)