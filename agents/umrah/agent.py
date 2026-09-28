"""
agents/umrah/agent.py
─────────────────────
Hajj & Umrah Specialist Agent — built with LangGraph.
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
from .prompts import UMRAH_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ── State ──────────────────────────────────────────────────────────────────────

class UmrahState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    tier: str | None
    pilgrims_count: int | None


# ── Tools ──────────────────────────────────────────────────────────────────────

@tool
def get_umrah_visa_guidance(has_valid_us_uk_schengen_visa: bool = False) -> dict:
    """
    Get current Saudi Umrah visa regulations, pathways, and Tasheer requirements for Pakistanis.

    Args:
        has_valid_us_uk_schengen_visa: True if applicant has an active US, UK, or Schengen visa with entry stamp.

    Returns:
        dict with visa options, fees in SAR/PKR, validity, and Tasheer biometric rules.
    """
    if has_valid_us_uk_schengen_visa:
        return {
            "recommended_pathway": "Saudi Tourist eVisa (Instant Online)",
            "cost_sar": 395,
            "cost_pkr_approx": 30000,
            "processing_time": "Instant (5 to 30 minutes)",
            "validity": "1 Year Multiple Entry (90 days stay per visit)",
            "features": [
                "Includes full Umrah authorization throughout the year (except Hajj days)",
                "No Tasheer biometric center visit required",
                "Includes mandatory comprehensive medical insurance",
                "Permits tourism to Riyadh, AlUla, Jeddah, Dammam as well"
            ],
            "requirements": ["Passport with 6+ months validity", "Valid US/UK/Schengen visa with entry stamp"]
        }
    else:
        return {
            "recommended_pathway": "Standard Umrah Visa (via Tasheer VFS Center)",
            "cost_breakdown": {
                "visa_and_insurance_sar": 405,
                "tasheer_biometric_fee_pkr": 14500,
                "total_estimated_pkr": 45000,
            },
            "processing_time": "3 to 5 business days after biometric submission",
            "validity": "90 Days Single Entry",
            "features": [
                "Issued directly by Saudi Ministry of Hajj & Umrah",
                "Valid for entry via any airport/port in Saudi Arabia",
                "Ladies under 45 can now travel without a Mahram"
            ],
            "tasheer_centers_in_pakistan": ["Islamabad", "Lahore", "Karachi", "Peshawar", "Quetta", "Sukkur"],
            "required_documents": [
                "Original Passport (6+ months validity)",
                "CNIC copy",
                "2 Passport-sized photos (white background)",
                "Meningitis ACWY & Polio vaccination certificates",
                "Confirmed return flight & hotel voucher"
            ]
        }


@tool
def calculate_umrah_package(
    tier: str = "4_star",
    nights_makkah: int = 5,
    nights_madinah: int = 5,
    num_pilgrims: int = 2,
    room_sharing: str = "double",
    transport_type: str = "haramain_train",
) -> dict:
    """
    Calculate a complete customized Umrah package cost breakdown for a group of pilgrims.

    Args:
        tier:            "economy" (3-Star Shuttle), "4_star" (Walking distance), or "5_star_vip" (Front row / Clock Tower)
        nights_makkah:   Number of nights in Makkah (default 5)
        nights_madinah:  Number of nights in Madinah (default 5)
        num_pilgrims:    Total number of pilgrims (default 2)
        room_sharing:    "double" (2 per room), "triple" (3 per room), "quad" (4 per room)
        transport_type:  "haramain_train", "private_gmc", or "shared_bus"

    Returns:
        dict with hotels, per-person cost in PKR/SAR, and total group cost.
    """
    tier = tier.lower()
    sharing = room_sharing.lower()

    # Base hotel pricing per night per room (PKR)
    if "5" in tier or "vip" in tier:
        makkah_hotel = "Fairmont Clock Royal Tower / Swissotel Al Maqam (5★, 0m to Haram)"
        madinah_hotel = "The Oberoi / Dar Al Taqwa (5★, 0-20m from Prophet's Mosque)"
        makkah_room_night = 68000
        madinah_room_night = 72000
    elif "4" in tier:
        makkah_hotel = "Anjum Hotel / Elaf Kinda (4-5★, 150-350m from Haram)"
        madinah_hotel = "Anwar Al Madinah Mövenpick / Zowar International (4-5★, 100-250m)"
        makkah_room_night = 36000
        madinah_room_night = 32000
    else:
        makkah_hotel = "Al Kiswah Towers Hotel (4★, 900m with 24/7 Free AC Shuttle)"
        madinah_hotel = "Emaar Elite Hotel (3★, 350m to Haram Courtyard)"
        makkah_room_night = 14500
        madinah_room_night = 16000

    # Sharing divider
    sharing_factor = 2 if "double" in sharing else (3 if "triple" in sharing else 4)
    rooms_needed = max(1, (num_pilgrims + sharing_factor - 1) // sharing_factor)

    hotel_total_makkah = makkah_room_night * nights_makkah * rooms_needed
    hotel_total_madinah = madinah_room_night * nights_madinah * rooms_needed
    hotel_total = hotel_total_makkah + hotel_total_madinah

    # Visa per person
    visa_per_person = 45000
    visa_total = visa_per_person * num_pilgrims

    # Transport
    if "gmc" in transport_type:
        transport_name = "Private VIP GMC Yukon (Airport Transfers + Makkah-Madinah + Ziyarat)"
        transport_total = 120000
    elif "train" in transport_type:
        transport_name = "Haramain High-Speed Bullet Train (Business/Economy) + Local Taxis"
        transport_total = 38000 * num_pilgrims
    else:
        transport_name = "AC Coaster / Shared Bus Transfer"
        transport_total = 18000 * num_pilgrims

    # Ziyarat
    ziyarat_total = 25000

    total_group_pkr = hotel_total + visa_total + transport_total + ziyarat_total
    per_person_pkr = int(total_group_pkr / num_pilgrims)
    per_person_sar = int(per_person_pkr / 74.5)  # approx 1 SAR = 74.5 PKR

    return {
        "tier": tier.upper(),
        "num_pilgrims": num_pilgrims,
        "room_sharing": sharing.upper(),
        "rooms_allocated": rooms_needed,
        "duration": f"{nights_makkah} Nights Makkah + {nights_madinah} Nights Madinah ({nights_makkah + nights_madinah} Nights Total)",
        "accommodations": {
            "makkah": makkah_hotel,
            "madinah": madinah_hotel,
            "meal_plan": "Daily Breakfast Buffet (BB) Included",
        },
        "transport": transport_name,
        "pricing": {
            "per_person_pkr": f"PKR {per_person_pkr:,}",
            "per_person_sar": f"SAR {per_person_sar:,}",
            "total_group_pkr": f"PKR {total_group_pkr:,}",
        },
        "inclusions": [
            "Umrah Visa processing & Tasheer biometric assistance",
            "Makkah hotel with daily buffet breakfast",
            "Madinah hotel with daily buffet breakfast",
            "Complete ground transportation per itinerary",
            "Guided Ziyarat in Makkah (Ghar Hira, Jabal Thawr, Arafat) & Madinah (Masjid Quba, Uhud)",
            "24/7 on-ground Kamal Express representative support in KSA",
            "5L Zamzam water container complimentary per pilgrim"
        ]
    }


@tool
def get_rawdah_permit_instructions() -> dict:
    """
    Get instructions and slot timing secrets for booking the Rawdah ash-Sharifah (Riyadul Jannah) permit on Nusuk.

    Returns:
        dict with Nusuk app timing rules, separate guidelines for men and women.
    """
    return {
        "title": "Nusuk Rawdah Ash-Sharifah (Riyadul Jannah) Permit Booking Guide",
        "app": "Nusuk (available on iOS and Android)",
        "permit_frequency": "Once every 365 days per pilgrim account",
        "weekly_slot_release": "New slots are released weekly by Ministry of Hajj every Friday at 12:00 PM (Saudi Time) and Saturdays.",
        "men_timings": "Usually available from Isha until Fajr, and morning from 10:00 AM to Dhuhr.",
        "women_timings": "Strictly segregated: Morning slot (after Fajr until 11:00 AM) and Evening slot (after Isha until 12:00 Midnight). Women enter via North Courtyard (Ladies Gate 25 / 37).",
        "pro_tips": [
            "Create your Nusuk profile as soon as your Saudi visa is issued.",
            "Log in exactly 5 minutes before the release hour on Friday.",
            "Arrive at the designated gate 15 minutes before your permit slot with the QR code ready on your phone."
        ]
    }


@tool
def get_ziyarat_guide(city: str = "both") -> dict:
    """
    Get historical and religious Ziyarat locations for Makkah and Madinah.

    Args:
        city: "makkah", "madinah", or "both"

    Returns:
        dict with historical details of all sacred landmarks.
    """
    makkah_sites = [
        {"name": "Jabal an-Nur (Cave of Hira)", "description": "The sacred mountain where the Prophet ﷺ received the first revelation of the Quran (Surah Al-Alaq)."},
        {"name": "Jabal Thawr (Cave of Thawr)", "description": "The mountain where Prophet Muhammad ﷺ and Abu Bakr (RA) took refuge during the Hijrah to Madinah."},
        {"name": "Mina & Jamarat", "description": "The tent city and pillars where pilgrims perform the Stoning of the Devil (Rami) during Hajj."},
        {"name": "Mount Arafat (Jabal ar-Rahmah)", "description": "The pillar of mercy where the Prophet ﷺ delivered the Farewell Sermon during Hajjatul Wida."},
        {"name": "Muzdalifah & Mash'ar al-Haram", "description": "Open area where pilgrims collect pebbles and spend the night during Hajj."},
        {"name": "Jannat al-Mu'alla Cemetery", "description": "Resting place of Sayyidatina Khadijah (RA) and ancestors of the Prophet ﷺ."}
    ]

    madinah_sites = [
        {"name": "Masjid Quba", "description": "The first mosque in Islam. Offering 2 Raka'at Nafl prayer here carries the spiritual reward equal to performing an Umrah."},
        {"name": "Mount Uhud & Shuhada Cemetery", "description": "Site of the Battle of Uhud; resting place of Sayyiduna Hamza (RA) and 70 Sahabah martyrs."},
        {"name": "Masjid al-Qiblatain", "description": "The historic mosque where the Qiblah was redirected from Jerusalem (Al-Aqsa) to Makkah (Al-Kaaba)."},
        {"name": "The Seven Mosques (Battle of the Trench / Khandaq)", "description": "Historic command posts of the Prophet ﷺ and companions during the Siege of Madinah."},
        {"name": "Jannat al-Baqi Cemetery", "description": "Directly adjacent to the Prophet's Mosque; resting place of 10,000+ Sahabah and wives of the Prophet ﷺ."}
    ]

    res = {}
    if city.lower() in ("makkah", "both"):
        res["makkah_ziyarat"] = makkah_sites
    if city.lower() in ("madinah", "both"):
        res["madinah_ziyarat"] = madinah_sites
    return res


# ── Graph Definition ──────────────────────────────────────────────────────────

TOOLS = [
    get_umrah_visa_guidance,
    calculate_umrah_package,
    get_rawdah_permit_instructions,
    get_ziyarat_guide,
]


def should_continue(state: UmrahState) -> str:
    messages = state["messages"]
    last = messages[-1]
    return "tools" if getattr(last, "tool_calls", None) else END


def call_model(state: UmrahState) -> dict:
    llm = get_provider().get_llm("appointments").bind_tools(TOOLS)
    messages = state["messages"]
    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=UMRAH_SYSTEM_PROMPT)] + messages
    response = llm.invoke(messages)
    return {"messages": [response]}


def build_umrah_graph() -> StateGraph:
    workflow = StateGraph(UmrahState)
    workflow.add_node("agent", call_model)
    workflow.add_node("tools", ToolNode(TOOLS))
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")
    return workflow.compile()


umrah_agent = build_umrah_graph()
