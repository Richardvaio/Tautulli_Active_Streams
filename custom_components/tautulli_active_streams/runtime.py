"""Typed runtime container for one Tautulli config entry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api import TautulliAPI
    from .card_cache import CardDataCache
    from .coordinators import TautulliHistoryCoordinator, TautulliSessionsCoordinator
    from .geo import IPGeoCache
    from .image import ImagePathCache


@dataclass(slots=True)
class TautulliRuntimeData:
    """Objects owned by a loaded config entry.

    This is stored alongside the legacy dictionary keys to retain compatibility
    with the integration's declared Home Assistant 2024.1 minimum.
    """

    api: TautulliAPI
    sessions: TautulliSessionsCoordinator
    history: TautulliHistoryCoordinator
    geo_cache: IPGeoCache
    image_cache: ImagePathCache
    card_cache: CardDataCache
