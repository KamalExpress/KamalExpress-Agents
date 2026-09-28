"""appointments package"""
from .agent import appointments_agent, gvc_driver
from .db import add_client, get_all_clients, get_client_by_passport, get_queue_stats, update_client_status
from .monitor import slot_monitor
from .portals.gvc import GVCPortalDriver
from .schemas import AvailableSlot, BookingResult, ClientProfile

__all__ = [
    "appointments_agent",
    "gvc_driver",
    "slot_monitor",
    "GVCPortalDriver",
    "ClientProfile",
    "AvailableSlot",
    "BookingResult",
    "add_client",
    "get_all_clients",
    "get_client_by_passport",
    "get_queue_stats",
    "update_client_status",
]
