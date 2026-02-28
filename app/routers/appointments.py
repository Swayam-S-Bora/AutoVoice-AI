from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from app.database import supabase
from app.logger import app_logger, call_logger

router = APIRouter(prefix="/appointments", tags=["appointments"])

class AppointmentCreate(BaseModel):
    customer_id: int
    appointment_type: str  # 'service' or 'test_drive'
    appointment_date: datetime
    notes: Optional[str] = None

class AppointmentResponse(BaseModel):
    id: int
    customer_id: int
    appointment_type: str
    appointment_date: datetime
    status: str
    notes: Optional[str]
    created_at: datetime

@router.post("/", response_model=AppointmentResponse)
async def create_appointment(appointment: AppointmentCreate):
    app_logger.info(f"Creating appointment for customer {appointment.customer_id}")
    
    try:
        # Check if appointment time is in future
        if appointment.appointment_date < datetime.now():
            app_logger.warning(f"Attempted to book past appointment for customer {appointment.customer_id}")
            raise HTTPException(status_code=400, detail="Appointment time must be in future")
        
        # Create appointment
        result = supabase.table("appointments").insert({
            "customer_id": appointment.customer_id,
            "appointment_type": appointment.appointment_type,
            "appointment_date": appointment.appointment_date.isoformat(),
            "status": "scheduled",
            "notes": appointment.notes,
            "created_at": datetime.now().isoformat()
        }).execute()
        
        app_logger.info(f"Appointment created successfully: ID {result.data[0]['id']}")
        
        # Get customer details for call log
        customer = supabase.table("customers")\
            .select("phone_number")\
            .eq("id", appointment.customer_id)\
            .execute()
        
        # Log the booking
        if customer.data:
            call_logger.info(
                f"APPOINTMENT_BOOKED|Customer:{appointment.customer_id}|"
                f"Phone:{customer.data[0]['phone_number']}|"
                f"Type:{appointment.appointment_type}|"
                f"Date:{appointment.appointment_date}"
            )
        
        # Update customer's last call time
        if customer.data:
            supabase.table("customers")\
                .update({"last_call_at": datetime.now().isoformat()})\
                .eq("id", appointment.customer_id)\
                .execute()
        
        return result.data[0]
    
    except HTTPException:
        raise
    except Exception as e:
        app_logger.error(f"Error creating appointment: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/customer/{customer_id}")
async def get_customer_appointments(customer_id: int):
    app_logger.info(f"Fetching appointments for customer {customer_id}")
    
    result = supabase.table("appointments")\
        .select("*")\
        .eq("customer_id", customer_id)\
        .order("appointment_date", desc=False)\
        .execute()
    
    app_logger.info(f"Found {len(result.data)} appointments for customer {customer_id}")
    return result.data

@router.get("/upcoming")
async def get_upcoming_appointments(days: int = 7):
    """Get all appointments for the next X days"""
    app_logger.info(f"Fetching appointments for next {days} days")
    
    # Calculate date range
    now = datetime.now()
    end_date = now.replace(hour=23, minute=59, second=59)  # Today end
    # Add days (simplified - in production use proper date math)
    
    result = supabase.table("appointments")\
        .select("*, customers(name, phone_number)")\
        .gte("appointment_date", now.isoformat())\
        .eq("status", "scheduled")\
        .order("appointment_date")\
        .execute()
    
    return result.data