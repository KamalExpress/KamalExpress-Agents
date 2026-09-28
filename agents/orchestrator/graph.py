"""
agents/orchestrator/graph.py
─────────────────────────────
Orchestrator — the front door to all Kamal Express agents.

Flow:
  User message → intent classification (fast, cheap model)
                → route to Visa Agent | Appointments Agent | General
                → stream response back

Uses gemma3:1b for triage (sub-100ms) and escalates to qwen2.5:7b for
ambiguous / multi-intent queries.
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from providers import get_provider
from agents.visa.agent import visa_agent
from agents.appointments.agent import appointments_agent
from agents.umrah.agent import umrah_agent
from agents.hotels.agent import hotel_agent
from .prompts import ORCHESTRATOR_SYSTEM_PROMPT, ROUTER_PROMPT

logger = logging.getLogger(__name__)

Intent = Literal["visa", "appointments", "umrah", "hotels", "general", "unclear"]


# ── State ──────────────────────────────────────────────────────────────────────

class OrchestratorState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    intent: Intent
    routed_to: str
    sub_agent_response: str


# ── Intent router (fast model) ─────────────────────────────────────────────────

def classify_intent(state: OrchestratorState) -> dict:
    """
    Classify intent using deterministic keywords or the fast LLM router.
    """
    last_user_msg = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    ).lower()

    # Fast deterministic routing
    appointment_keywords = ["cdp", "portal", "gvc", "slot", "intake", "queue", "appointment", "monitor", "islamabad vac", "karachi vac", "lahore vac"]
    visa_keywords = ["requirement", "checklist", "document", "schengen", "greece type", "work permit", "greek", "embassy fee", "eligibility", "how to apply"]
    umrah_keywords = ["umrah", "hajj", "nusuk", "rawdah", "riyadul jannah", "ziyarat", "tasheer", "makkah package", "madinah package", "pilgrim", "zamzam", "makkah hotel package"]
    hotel_keywords = ["hotel", "room", "stay", "clock tower", "fairmont", "swissotel", "oberoi", "shuttle hotel", "accommodation", "booking hold", "voucher", "quad room", "double room"]

    if any(k in last_user_msg for k in umrah_keywords):
        logger.info(f"[orchestrator] Fast keyword match → umrah")
        return {"intent": "umrah"}

    if any(k in last_user_msg for k in hotel_keywords):
        logger.info(f"[orchestrator] Fast keyword match → hotels")
        return {"intent": "hotels"}

    if any(k in last_user_msg for k in appointment_keywords):
        logger.info(f"[orchestrator] Fast keyword match → appointments")
        return {"intent": "appointments"}

    if any(k in last_user_msg for k in visa_keywords):
        logger.info(f"[orchestrator] Fast keyword match → visa")
        return {"intent": "visa"}

    fast_llm = get_provider().get_llm("orchestrator", temperature=0.0, streaming=False)
    prompt = ROUTER_PROMPT.format(user_message=last_user_msg)
    try:
        response = fast_llm.invoke([HumanMessage(content=prompt)])
        raw = response.content.strip().lower()

        if "umrah" in raw or "hajj" in raw:
            intent: Intent = "umrah"
        elif "hotel" in raw or "room" in raw:
            intent = "hotels"
        elif "appointment" in raw or "slot" in raw or "gvc" in raw:
            intent = "appointments"
        elif "visa" in raw:
            intent = "visa"
        elif "general" in raw:
            intent = "general"
        else:
            intent = "unclear"
    except Exception as e:
        logger.warning(f"[orchestrator] Intent classification failed: {e} — defaulting to general")
        intent = "general"

    logger.info(f"[orchestrator] Intent: '{last_user_msg[:60]}...' → {intent}")
    return {"intent": intent}


# ── Routing logic ──────────────────────────────────────────────────────────────

def route_to_agent(state: OrchestratorState) -> str:
    """Return the next node name based on classified intent."""
    intent = state.get("intent", "general")
    routes = {
        "visa":         "visa_agent",
        "appointments": "appointments_agent",
        "umrah":        "umrah_agent",
        "hotels":       "hotel_agent",
        "general":      "general_response",
        "unclear":      "clarify",
    }
    return routes.get(intent, "general_response")


# ── Sub-agent callers ──────────────────────────────────────────────────────────

def call_visa_agent(state: OrchestratorState) -> dict:
    logger.info("[orchestrator] Routing to Visa Agent")
    result = visa_agent.invoke({"messages": state["messages"]})
    last = result["messages"][-1]
    return {
        "messages": [last],
        "routed_to": "visa_agent",
        "sub_agent_response": last.content,
    }


def call_appointments_agent(state: OrchestratorState) -> dict:
    logger.info("[orchestrator] Routing to Appointments Agent")
    result = appointments_agent.invoke({"messages": state["messages"]})
    last = result["messages"][-1]
    return {
        "messages": [last],
        "routed_to": "appointments_agent",
        "sub_agent_response": last.content,
    }


def call_umrah_agent(state: OrchestratorState) -> dict:
    logger.info("[orchestrator] Routing to Hajj & Umrah Agent")
    result = umrah_agent.invoke({"messages": state["messages"]})
    last = result["messages"][-1]
    return {
        "messages": [last],
        "routed_to": "umrah_agent",
        "sub_agent_response": last.content,
    }


def call_hotel_agent(state: OrchestratorState) -> dict:
    logger.info("[orchestrator] Routing to Hotel Agent")
    result = hotel_agent.invoke({"messages": state["messages"]})
    last = result["messages"][-1]
    return {
        "messages": [last],
        "routed_to": "hotel_agent",
        "sub_agent_response": last.content,
    }


def general_response(state: OrchestratorState) -> dict:
    """Handle general queries directly without routing to a specialist."""
    llm = get_provider().get_llm("orchestrator")
    messages = state["messages"]
    if not any(isinstance(m, SystemMessage) for m in messages):
        messages = [SystemMessage(content=ORCHESTRATOR_SYSTEM_PROMPT)] + messages
    response = llm.invoke(messages)
    return {"messages": [response], "routed_to": "general"}


def clarify(state: OrchestratorState) -> dict:
    """Ask the user to clarify their intent."""
    clarification = AIMessage(
        content=(
            "Welcome to **Kamal Express**! How can I assist your journey today?\n\n"
            "- 📅 **Visa Appointments**: Greece GVC World slot discovery & automated queue\n"
            "- 🛂 **Visa Requirements**: Checklists for Greece (Type 26 / Schengen), Saudi, UAE, UK\n"
            "- 🕋 **Hajj & Umrah**: Custom packages, Nusuk Rawdah permits, Tasheer rules & Ziyarat\n"
            "- 🏨 **Hotel Bookings**: Top hotels in Makkah (Clock Tower), Madinah, Athens, Dubai"
        )
    )
    return {"messages": [clarification], "routed_to": "clarify"}


# ── Build graph ────────────────────────────────────────────────────────────────

def build_orchestrator():
    graph = StateGraph(OrchestratorState)

    graph.add_node("classify",            classify_intent)
    graph.add_node("visa_agent",          call_visa_agent)
    graph.add_node("appointments_agent",  call_appointments_agent)
    graph.add_node("umrah_agent",         call_umrah_agent)
    graph.add_node("hotel_agent",         call_hotel_agent)
    graph.add_node("general_response",    general_response)
    graph.add_node("clarify",             clarify)

    graph.set_entry_point("classify")
    graph.add_conditional_edges("classify", route_to_agent)

    graph.add_edge("visa_agent",         END)
    graph.add_edge("appointments_agent", END)
    graph.add_edge("umrah_agent",        END)
    graph.add_edge("hotel_agent",        END)
    graph.add_edge("general_response",   END)
    graph.add_edge("clarify",            END)

    return graph.compile()


orchestrator = build_orchestrator()

