"""Tests for Tautulli coordinator data normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.tautulli_active_streams.const import (
    CONF_ENABLE_STATISTICS,
    CONF_STATISTICS_CYCLE_DAY,
    CONF_STATISTICS_DAYS,
    CONF_STATISTICS_PERIOD,
    CONF_STATS_MONTH_TO_DATE,
    MAX_HISTORY_RECORDS,
    STATISTICS_PERIOD_CALENDAR_MONTH,
    STATISTICS_PERIOD_CUSTOM_MONTH,
    STATISTICS_PERIOD_ROLLING,
)
from custom_components.tautulli_active_streams.coordinators import (
    TautulliHistoryCoordinator,
    statistics_period,
    statistics_start,
)


def test_history_aggregates_by_stable_user_id() -> None:
    """Renames stay together and duplicate display names stay separate."""
    coordinator = object.__new__(TautulliHistoryCoordinator)
    history = {
        "data": [
            {
                "user_id": 10,
                "user": "Old Name",
                "started": "100",
                "stopped": 150,
                "duration": "50",
                "media_type": "movie",
                "watched_status": "1",
            },
            {
                "user_id": 10,
                "user": "New Name",
                "started": 200,
                "stopped": 250,
                "duration": 50,
                "media_type": "episode",
                "watched_status": 1,
            },
            {
                "user_id": 20,
                "user": "New Name",
                "started": 300,
                "stopped": 350,
                "duration": 50,
                "media_type": "movie",
                "watched_status": 1,
            },
        ]
    }

    result = coordinator._parse_user_history(history)

    assert set(result) == {"10", "20"}
    assert result["10"]["username"] == "New Name"
    assert result["10"]["total_plays"] == 2
    assert result["20"]["username"] == "New Name"
    assert result["20"]["total_plays"] == 1


def test_legacy_statistics_period_migration() -> None:
    """Legacy month-to-date settings retain their original meaning."""
    assert statistics_period({CONF_STATS_MONTH_TO_DATE: True}) == (
        STATISTICS_PERIOD_CALENDAR_MONTH
    )
    assert statistics_period({CONF_STATS_MONTH_TO_DATE: False}) == (
        STATISTICS_PERIOD_ROLLING
    )


def test_statistics_start_for_rolling_and_calendar_month() -> None:
    """Standard periods calculate their expected local boundaries."""
    now = datetime(2026, 8, 20, 15, 30, tzinfo=timezone.utc)
    assert statistics_start(
        now,
        {
            CONF_STATISTICS_PERIOD: STATISTICS_PERIOD_ROLLING,
            CONF_STATISTICS_DAYS: 30,
        },
    ) == datetime(2026, 7, 21, 15, 30, tzinfo=timezone.utc)
    assert statistics_start(
        now, {CONF_STATISTICS_PERIOD: STATISTICS_PERIOD_CALENDAR_MONTH}
    ) == datetime(2026, 8, 1, tzinfo=timezone.utc)


def test_custom_monthly_cycle_uses_current_or_previous_cycle() -> None:
    """A custom start day rolls over at local midnight on that day."""
    options = {
        CONF_STATISTICS_PERIOD: STATISTICS_PERIOD_CUSTOM_MONTH,
        CONF_STATISTICS_CYCLE_DAY: 15,
    }
    assert statistics_start(
        datetime(2026, 8, 20, 12, tzinfo=timezone.utc), options
    ) == datetime(2026, 8, 15, tzinfo=timezone.utc)
    assert statistics_start(
        datetime(2026, 8, 10, 12, tzinfo=timezone.utc), options
    ) == datetime(2026, 7, 15, tzinfo=timezone.utc)
    assert statistics_start(
        datetime(2026, 1, 10, 12, tzinfo=timezone.utc), options
    ) == datetime(2025, 12, 15, tzinfo=timezone.utc)


def test_custom_monthly_cycle_clamps_to_month_end() -> None:
    """Cycle days 29 to 31 remain predictable in shorter months."""
    options = {
        CONF_STATISTICS_PERIOD: STATISTICS_PERIOD_CUSTOM_MONTH,
        CONF_STATISTICS_CYCLE_DAY: 31,
    }
    assert statistics_start(
        datetime(2024, 3, 1, 12, tzinfo=timezone.utc), options
    ) == datetime(2024, 2, 29, tzinfo=timezone.utc)
    assert statistics_start(
        datetime(2025, 3, 1, 12, tzinfo=timezone.utc), options
    ) == datetime(2025, 2, 28, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_history_query_uses_selected_period_and_explicit_grouping() -> None:
    """The upstream query is bounded and independent of Tautulli UI settings."""
    coordinator = object.__new__(TautulliHistoryCoordinator)
    coordinator.config_entry = SimpleNamespace(
        options={
            CONF_ENABLE_STATISTICS: True,
            CONF_STATISTICS_PERIOD: STATISTICS_PERIOD_CUSTOM_MONTH,
            CONF_STATISTICS_CYCLE_DAY: 15,
        }
    )
    coordinator.api = SimpleNamespace(get_history=AsyncMock(return_value={"data": []}))
    coordinator._geo_cache = None

    with patch(
        "custom_components.tautulli_active_streams.coordinators.ha_now",
        return_value=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
    ):
        result = await coordinator._async_update_data()

    assert result["user_stats"] == {}
    coordinator.api.get_history.assert_awaited_once_with(
        after="2026-08-15",
        grouping=0,
        order_column="date",
        order_dir="desc",
        length=MAX_HISTORY_RECORDS,
    )
