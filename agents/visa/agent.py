"""
agents/visa/agent.py
─────────────────────
Visa Agent — handles:
  - Document requirements lookup
  - Application form guidance
  - Fee calculation
  - Status tracking
"""
from __future__ import annotations

import logging
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from providers import get_provider
from .prompts import VISA_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ── State ──────────────────────────────────────────────────────────────────────

class VisaState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    nationality: str
    destination: str
    visa_type: str
    application_id: str | None


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool
def get_visa_requirements(nationality: str, destination_country: str, visa_type: str) -> dict:
    """
    Get the visa requirements for a given nationality traveling to a destination.

    Args:
        nationality:         Applicant's nationality, e.g. "Pakistani"
        destination_country: Country they want to visit, e.g. "Greece", "Saudi Arabia", "United Arab Emirates", "United Kingdom"
        visa_type:           Type of visa, e.g. "26" (Seasonal Work), "0" (Schengen C), "tourist", "umrah", "standard_visitor"

    Returns:
        dict with: documents (list), fees, processing_time, validity, notes
    """
    logger.info(f"[visa] Requirements query: {nationality} → {destination_country} ({visa_type})")
    from agents.appointments.db import query_visa_rules

    rules = query_visa_rules(country=destination_country, visa_type=visa_type, nationality=nationality)
    if rules:
        r = rules[0]
        return {
            "found": True,
            "destination_country": r["destination_country"],
            "visa_category": r["visa_category"],
            "nationality": r["nationality"],
            "embassy_fee": r["embassy_fee"],
            "vac_fee": r["vac_fee"],
            "processing_time": r["processing_time"],
            "validity": r["validity"],
            "stay_duration": r["stay_duration"],
            "appointment_required": bool(r["appointment_required"]),
            "appointment_portal": r["appointment_portal"],
            "required_documents": r["required_documents"],
            "financial_requirements": r["financial_requirements"],
            "special_notes": r["special_notes"],
        }

    # Fallback to general query for country
    country_rules = query_visa_rules(country=destination_country, nationality=nationality)
    if country_rules:
        r = country_rules[0]
        return {
            "found": True,
            "destination_country": r["destination_country"],
            "visa_category": r["visa_category"],
            "nationality": r["nationality"],
            "embassy_fee": r["embassy_fee"],
            "vac_fee": r["vac_fee"],
            "processing_time": r["processing_time"],
            "validity": r["validity"],
            "required_documents": r["required_documents"],
            "financial_requirements": r["financial_requirements"],
            "special_notes": r["special_notes"],
        }

    return {
        "found": False,
        "message": f"Detailed checklist for {nationality} → {destination_country} ({visa_type}) not found in SQLite knowledge base. Standard Schengen/International documents apply: valid passport (6m+), photographs, bank statement (6m), return ticket, hotel booking, and travel insurance.",
    }


@tool
def calculate_visa_fees(destination_country: str, visa_type: str, num_applicants: int = 1) -> dict:
    """
    Calculate total visa fees for one or more applicants.

    Args:
        destination_country: Destination country
        visa_type:           Visa category
        num_applicants:      Number of applicants (for group bookings)

    Returns:
        dict with: fee_breakdown, total_pkr (estimated), total_usd
    """
    logger.info(f"[visa] Fee calc: {destination_country} ({visa_type}) × {num_applicants}")
    # Stub — replace with live exchange rate API
    return {
        "destination": destination_country,
        "visa_type": visa_type,
        "num_applicants": num_applicants,
        "base_fee_usd": 120,
        "service_fee_usd": 25,
        "total_usd": (120 + 25) * num_applicants,
        "estimated_pkr": int((120 + 25) * num_applicants * 278),  # approximate rate
        "note": "Exchange rate is approximate. Final amount charged at time of payment.",
    }


@tool
def check_visa_status(application_id: str) -> dict:
    """
    Check the status of a visa application using the reference/application ID.

    Args:
        application_id: The visa application reference number

    Returns:
        dict with: status, last_updated, message, next_steps
    """
    logger.info(f"[visa] Status check for: {application_id}")
    # TODO: scrape portal or call API
    return {
        "application_id": application_id,
        "status": "Under Review",
        "last_updated": "2024-01-28",
        "message": "Your application is being processed.",
        "next_steps": "No action required. You will receive an email when a decision is made.",
        "estimated_completion": "5-10 working days",
    }


@tool
def list_visa_types(destination_country: str) -> dict:
    """
    List all available visa types for a destination country.

    Args:
        destination_country: Country name

    Returns:
        dict with list of visa categories and brief descriptions
    """
    visa_types = {
        "United Kingdom": ["tourist", "business", "student", "work", "family", "transit"],
        "United Arab Emirates": ["tourist", "business", "transit", "residence"],
        "United States": ["B1/B2 tourist", "F1 student", "H1B work", "J1 exchange"],
        "Schengen": ["tourist", "business", "student", "family", "transit"],
        "Saudi Arabia": ["tourist", "umrah", "hajj", "business", "work"],
    }
    types = visa_types.get(destination_country, ["tourist", "business", "student", "transit"])
    return {"destination": destination_country, "visa_types": types}


@tool
def get_processing_centers(destination_country: str, applicant_city: str) -> dict:
    """
    Get visa processing center locations for a given destination and applicant city.

    Args:
        destination_country: The country you're applying for
        applicant_city:      City where the applicant is located (Karachi, Lahore, Islamabad)

    Returns:
        dict with list of centers and addresses
    """
    centers = {
        ("United Kingdom", "Karachi"): [
            {"name": "VFS Global - Karachi", "address": "Plot 8-C, 5th Floor, Dolmen City, Karachi", "phone": "+92-21-111-837-473"},
        ],
        ("United Kingdom", "Lahore"): [
            {"name": "VFS Global - Lahore", "address": "Upper Ground Floor, Packages Mall, Lahore", "phone": "+92-42-111-837-473"},
        ],
        ("United Kingdom", "Islamabad"): [
            {"name": "VFS Global - Islamabad", "address": "1st Floor, Centaurus Mall, F-8, Islamabad", "phone": "+92-51-111-837-473"},
        ],
    }

    key = (destination_country, applicant_city)
    result = centers.get(key, [{"name": f"Contact Kamal Express for {destination_country} centers in {applicant_city}", "address": "", "phone": ""}])
    return {"destination": destination_country, "city": applicant_city, "centers": result}


TOOLS = [
    get_visa_requirements,
    calculate_visa_fees,
    check_visa_status,
    list_visa_types,
    get_processing_centers,
]


# ── Agent graph ────────────────────────────────────────────────────────────────

def build_visa_agent():
    llm = get_provider().get_llm("visa").bind_tools(TOOLS)
    tool_node = ToolNode(TOOLS)

    def call_model(state: VisaState):
        messages = state["messages"]
        if not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=VISA_SYSTEM_PROMPT)] + messages
        response = llm.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: VisaState):
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(VisaState)
    graph.add_node("agent", call_model)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue)
    graph.add_edge("tools", "agent")

    return graph.compile()


visa_agent = build_visa_agent()
