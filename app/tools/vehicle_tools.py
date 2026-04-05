from app.db.supabase_client import supabase
from app.core.logger import log_action

def get_vehicle_info(input_data):
    res = supabase.table("vehicles")\
        .select("*")\
        .ilike("name", f"%{input_data['query']}%")\
        .execute()

    log_action("get_vehicle_info", input_data, res.data)

    return res.data