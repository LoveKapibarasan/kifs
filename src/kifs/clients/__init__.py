"""HTTP clients for the two upstream services."""

from kifs.clients.kishin import KishinAnalyticsClient
from kifs.clients.shogiwars import ShogiWarsClient

__all__ = ["KishinAnalyticsClient", "ShogiWarsClient"]
