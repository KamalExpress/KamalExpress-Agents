"""
agents/hotels/agent.py
──────────────────────
Hotel Booking Specialist Agent — built with LangGraph.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from providers import get_provider
from .prompts import HOTEL_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ── State ──────────────────────────────────────────────────────────────────────

class HotelState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    city: str | None
    hotel_id: int | None


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool
def search_hotels(
    city: str,
    min_stars: int = 1,
    max_price_pkr: Optional[int] = None,
    shuttle_only: bool = False,
) -> dict:
    """
    Search available hotels in Makkah, Madinah, Athens, or Dubai with criteria filters.

    Args:
        city:          "Makkah", "Madinah", "Athens", or "Dubai"
        min_stars:     Minimum star rating (1 to 5)
        max_price_pkr: Maximum budget in PKR per night (optional)
        shuttle_only:  True to filter only hotels with 24/7 free shuttle to Haram / Center

    Returns:
        dict with matching hotels, star ratings, walking distance in meters, price per night in PKR/SAR, amenities, and room types.
    """
    logger.info(f"[hotels] Searching hotels in {city} (Stars >= {min_stars}, Max PKR: {max_price_pkr})")
    from agents.appointments.db import search_hotels_db

    results = search_hotels_db(
        city=city,
        min_stars=min_stars,
        max_price_pkr=max_price_pkr,
        shuttle_only=shuttle_only,
    )

    if not results:
        return {
            "found": False,
            "city": city,
            "message": f"No hotels matching the criteria in {city}. Try broadening your search or star rating.",
        }

    return {
        "found": True,
        "city": city,
        "total_results": len(results),
        "hotels": [
            {
                "id": h["id"],
                "name": h["name"],
                "stars": h["stars"],
                "area": h["area"],
                "distance_to_center": f"{h['distance_to_center_m']} meters ({'Front Row' if h['distance_to_center_m'] == 0 else ('Short Walk' if h['distance_to_center_m'] <= 350 else 'Shuttle/Taxi')})",
                "shuttle_service": "24/7 Free AC Shuttle" if h["shuttle_service"] else "Walking Access",
                "price_per_night_pkr": f"PKR {h['price_per_night_pkr']:,}",
                "price_per_night_sar": f"SAR {h['price_per_night_sar']:,}" if h.get("price_per_night_sar") else "N/A",
                "rating": f"⭐ {h['rating']} / 5.0",
                "amenities": h.get("amenities", []),
                "room_types": h.get("room_types", []),
            }
            for h in results
        ]
    }


@tool
def calculate_hotel_stay_cost(
    hotel_name_or_id: str,
    checkin_date: str,
    checkout_date: str,
    room_type: str = "Standard Double",
    rooms_count: int = 1,
    meal_plan: str = "BB",
) -> dict:
    """
    Calculate the total cost breakdown for a hotel stay.

    Args:
        hotel_name_or_id: Hotel name (e.g. "Fairmont Makkah Clock Royal Tower") or ID number
        checkin_date:     Check-in date DD/MM/YYYY
        checkout_date:    Check-out date DD/MM/YYYY
        room_type:        Room type (e.g. "Standard Double", "Deluxe Kaaba View", "Quad Family")
        rooms_count:      Number of rooms required
        meal_plan:        "RO" (Room Only), "BB" (Bed & Breakfast), "HB" (Half Board - Breakfast+Dinner), "FB" (Full Board)

    Returns:
        dict with nights, price per night, meal supplement, taxes, and total payable in PKR & SAR/EUR.
    """
    from datetime import datetime
    from agents.appointments.db import get_connection

    # Calculate nights
    try:
        d1 = datetime.strptime(checkin_date.strip(), "%d/%m/%Y")
        d2 = datetime.strptime(checkout_date.strip(), "%d/%m/%Y")
        nights = max(1, (d2 - d1).days)
    except Exception:
        nights = 3

    conn = get_connection()
    try:
        if hotel_name_or_id.isdigit():
            h = conn.execute("SELECT * FROM hotels WHERE id = ?", (int(hotel_name_or_id),)).fetchone()
        else:
            h = conn.execute("SELECT * FROM hotels WHERE LOWER(name) LIKE LOWER(?)", (f"%{hotel_name_or_id.strip()}%",)).fetchone()

        if not h:
            return {"found": False, "message": f"Hotel '{hotel_name_or_id}' not found in database."}

        base_rate = h["price_per_night_pkr"]
        # Room premium
        room_mult = 1.35 if ("kaaba" in room_type.lower() or "haram view" in room_type.lower() or "suite" in room_type.lower()) else 1.0
        # Meal multiplier
        meal_mult = 1.0 if meal_plan.upper() == "RO" else (1.12 if meal_plan.upper() == "BB" else (1.25 if meal_plan.upper() == "HB" else 1.38))

        nightly_effective = int(base_rate * room_mult * meal_mult)
        subtotal = nightly_effective * nights * rooms_count
        taxes_and_fees = int(subtotal * 0.15)  # 15% VAT / Municipality
        total_pkr = subtotal + taxes_and_fees
        total_sar = int(total_pkr / 74.5)

        meal_desc = {
            "RO": "Room Only (No Meals)",
            "BB": "Bed & Breakfast (Daily Buffet Included)",
            "HB": "Half Board (Daily Breakfast & Dinner Buffet)",
            "FB": "Full Board (Breakfast, Lunch & Dinner Buffet)",
        }.get(meal_plan.upper(), "Bed & Breakfast")

        return {
            "found": True,
            "hotel_name": h["name"],
            "city": h["city"],
            "area": h["area"],
            "stars": f"{h['stars']} ★",
            "dates": f"{checkin_date} to {checkout_date} ({nights} Nights)",
            "rooms": f"{rooms_count} × {room_type}",
            "meal_plan": meal_desc,
            "price_breakdown": {
                "base_rate_per_room_night_pkr": f"PKR {nightly_effective:,}",
                "subtotal_pkr": f"PKR {subtotal:,}",
                "vat_and_municipality_pkr": f"PKR {taxes_and_fees:,} (15%)",
                "total_payable_pkr": f"PKR {total_pkr:,}",
                "total_payable_sar": f"SAR {total_sar:,}",
            },
            "cancellation_policy": "Free cancellation up to 72 hours before check-in date.",
        }
    finally:
        conn.close()


@tool
def book_hotel_room(
    hotel_id: int,
    guest_name: str,
    guest_phone: str,
    checkin_date: str,
    checkout_date: str,
    room_type: str = "Standard Double",
    meal_plan: str = "BB",
) -> dict:
    """
    Generate an instant provisional hotel booking confirmation and reservation voucher.

    Args:
        hotel_id:      Hotel database ID
        guest_name:    Primary guest full name
        guest_phone:   Guest contact number (e.g. "03001234567")
        checkin_date:  Check-in date DD/MM/YYYY
        checkout_date: Check-out date DD/MM/YYYY
        room_type:     Room category
        meal_plan:     "RO", "BB", "HB", or "FB"

    Returns:
        dict with booking voucher reference (KE-HTL-XXXX), dates, total cost, and confirmation status.
    """
    logger.info(f"[hotels] Booking hotel #{hotel_id} for guest '{guest_name}'")
    from agents.appointments.db import create_hotel_booking_record

    try:
        res = create_hotel_booking_record(
            hotel_id=hotel_id,
            guest_name=guest_name,
            guest_phone=guest_phone,
            checkin_date=checkin_date,
            checkout_date=checkout_date,
            room_type=room_type,
            meal_plan=meal_plan,
        )
        return {
            "success": True,
            "message": f"Hotel reservation confirmed successfully for {guest_name}!",
            "booking_details": res,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Graph Definition ──────────────────────────────────────────────────────────

TOOLS = [
    search_hotels,
    calculate_hotel_stay_cost,
    book_hotel_room,
]


def should_continue(state: HotelState) -> str:
    messages = state["messages"]
    last = messages[-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def call_model(state: HotelState) -> dict:
    llm = get_provider().get_llm("appointments").bind_tools(TOOLS)
    messages = state["messages"]
    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=HOTEL_SYSTEM_PROMPT)] + messages
    response = llm.invoke(messages)
    return {"messages": [response]}


def build_hotel_graph() -> StateGraph:
    workflow = StateGraph(HotelState)
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(TOOLS))
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")
    return workflow.compile()


hotel_agent = build_hotel_graph()
