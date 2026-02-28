from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from app.database import supabase
from app.logger import app_logger, call_logger

router = APIRouter(prefix="/customers", tags=["customers"])

class CustomerCreate(BaseModel):
    phone_number: str
    name: Optional[str] = None
    email: Optional[str] = None
    preferred_language: str = "en"

class CustomerResponse(BaseModel):
    id: int
    phone_number: str
    name: Optional[str]
    email: Optional[str]
    preferred_language: str
    created_at: datetime

@router.post("/", response_model=CustomerResponse)
async def create_customer(customer: CustomerCreate):
    app_logger.info(f"Creating/retrieving customer with phone: {customer.phone_number}")
    
    try:
        # Check if customer already exists
        existing = supabase.table("customers")\
            .select("*")\
            .eq("phone_number", customer.phone_number)\
            .execute()
        
        if existing.data:
            app_logger.info(f"Existing customer found: ID {existing.data[0]['id']}")
            return existing.data[0]
        
        # Create new customer
        result = supabase.table("customers").insert({
            "phone_number": customer.phone_number,
            "name": customer.name,
            "email": customer.email,
            "preferred_language": customer.preferred_language,
            "created_at": datetime.now().isoformat()
        }).execute()
        
        app_logger.info(f"New customer created with ID: {result.data[0]['id']}")
        
        # Log the creation event
        call_logger.info(f"NEW_CUSTOMER|Phone:{customer.phone_number}|Name:{customer.name}")
        
        return result.data[0]
    
    except Exception as e:
        app_logger.error(f"Error creating customer: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{phone_number}")
async def get_customer(phone_number: str):
    app_logger.info(f"Fetching customer with phone: {phone_number}")
    
    result = supabase.table("customers")\
        .select("*")\
        .eq("phone_number", phone_number)\
        .execute()
    
    if not result.data:
        app_logger.warning(f"Customer not found: {phone_number}")
        raise HTTPException(status_code=404, detail="Customer not found")
    
    app_logger.info(f"Customer found: ID {result.data[0]['id']}")
    return result.data[0]

@router.put("/{phone_number}/last-call")
async def update_last_call(phone_number: str):
    app_logger.info(f"Updating last call time for: {phone_number}")
    
    result = supabase.table("customers")\
        .update({"last_call_at": datetime.now().isoformat()})\
        .eq("phone_number", phone_number)\
        .execute()
    
    call_logger.info(f"CALL_UPDATED|Phone:{phone_number}")
    
    return {"message": "Updated successfully"}