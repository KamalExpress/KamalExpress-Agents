"""
agents/appointments/schemas.py
──────────────────────────────
Pydantic data schemas for client profiles, appointment booking payloads,
and slot discovery queries.
"""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Literal, Optional


class ClientProfile(BaseModel):
    id: Optional[int] = None
    first_name: str = Field(..., description="Applicant given name(s)")
    last_name: str = Field(..., description="Applicant surname / family name")
    dob: str = Field(..., description="Date of birth in DD/MM/YYYY format")
    passport_number: str = Field(..., description="Passport number (alphanumeric)")
    passport_expiry: str = Field(..., description="Passport expiration date in DD/MM/YYYY format")
    passport_issue_date: Optional[str] = Field(default="", description="Passport issue date DD/MM/YYYY")
    passport_issue_place: Optional[str] = Field(default="", description="City of passport issuance")
    
    gender: Literal["Male", "Female", "Other"] = Field(default="Male")
    gender_id: str = Field(default="2", description="GVC gender ID: '1'=Female, '2'=Male")
    
    nationality: str = Field(default="Pakistani")
    nationality_id: str = Field(default="197", description="GVC nationality ID: '197'=Pakistan")
    
    phone_number: str = Field(..., description="Phone number without leading 0 (e.g. 3001234567)")
    phone_prefix_id: str = Field(default="197", description="Phone country prefix ID ('197'=+92)")
    email: str = Field(..., description="Email address for notifications/login")
    
    destination: str = Field(default="Greece", description="Target destination country")
    visa_type: str = Field(default="26", description="GVC appointment type code: '26'=Seasonal/Dependent D, '0'=Schengen C, '2'=National D")
    vac_id: str = Field(default="138", description="Visa Application Center ID: '138'=Islamabad, '137'=Karachi, '139'=Lahore")
    vac_city: str = Field(default="Islamabad", description="VAC city name")
    
    preferred_date_start: Optional[str] = Field(default=None, description="Earliest desired appointment date (DD/MM/YYYY)")
    preferred_date_end: Optional[str] = Field(default=None, description="Latest desired appointment date (DD/MM/YYYY)")
    
    status: Literal["QUEUED", "IN_PROGRESS", "BOOKED", "FAILED", "PAUSED"] = Field(default="QUEUED")
    booking_reference: Optional[str] = Field(default=None, description="Embassy booking reference number / ARN")
    booked_date: Optional[str] = Field(default=None, description="Confirmed appointment date")
    booked_time: Optional[str] = Field(default=None, description="Confirmed appointment time")
    notes: Optional[str] = Field(default="", description="Operator or agent notes")
    raw_confirmation_path: Optional[str] = Field(default="", description="Path to archived raw server confirmation payload")
    booked_by_account_id: Optional[int] = Field(default=None, description="GVC portal account ID that booked this client")
    booked_by_worker_name: Optional[str] = Field(default="", description="Name of remote worker persona that booked this client")
    booking_cost_pkr: Optional[int] = Field(default=0, description="Calculated operational cost for securing this booking")
    created_at: Optional[str] = Field(default_factory=lambda: datetime.utcnow().isoformat())

    @model_validator(mode="before")
    @classmethod
    def normalize_client_data(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "surname" in data and "last_name" not in data:
                data["last_name"] = data["surname"]
            elif "last_name" in data and "surname" not in data:
                data["surname"] = data["last_name"]
            
            # Normalize gender & gender_id
            g = str(data.get("gender", "")).strip().lower()
            if g in ["female", "f", "1", "woman"]:
                data["gender"] = "Female"
                data["gender_id"] = "1"
            elif g in ["other", "o", "3"]:
                data["gender"] = "Other"
                data["gender_id"] = "3"
            elif g in ["male", "m", "2", "man"]:
                data["gender"] = "Male"
                data["gender_id"] = "2"
            elif not data.get("gender"):
                data["gender"] = "Male"
                data["gender_id"] = "2"

            # Normalize nationality & nationality_id
            nat = str(data.get("nationality", "")).strip()
            if nat.lower() in ["pakistan", "pakistani", "pk", "197", ""]:
                data["nationality"] = "Pakistani"
                data["nationality_id"] = "197"
        return data

    @property
    def surname(self) -> str:
        return self.last_name

    @field_validator("phone_number")
    def clean_phone(cls, v: str) -> str:
        cleaned = "".join(c for c in v if c.isdigit())
        return cleaned.lstrip("0")


class SlotSearchQuery(BaseModel):
    destination: str = "Greece"
    vac_id: str = "138"  # Islamabad default
    visa_type: str = "26"  # Type 26 default
    date_from: Optional[str] = None  # DD/MM/YYYY
    date_to: Optional[str] = None    # DD/MM/YYYY
    members: int = 1


class AvailableSlot(BaseModel):
    date: str
    time: str
    slot_id: str
    vac_id: str
    vac_name: str
    visa_type: str
    available_capacity: int = 1


class BookingResult(BaseModel):
    success: bool
    client_id: Optional[int] = None
    client_name: str
    reference_number: Optional[str] = None
    portal: str = "Greece (GVC World)"
    vac_city: str
    visa_type: str
    booked_date: Optional[str] = None
    booked_time: Optional[str] = None
    message: str
    requires_otp: bool = False
    details: Optional[dict] = None
    raw_payload: Optional[Any] = None
