"""Safety checks for the local Windows paper-startup flow."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tools import windows_paper_startup as startup

dashboard = importlib.import_module("polymarket_exec.ops.dashboard.app")


@pytest.mark.asyncio
async def test_paper_autostart_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BTC_PAPER_AUTOSTART", raising=False)
    set_mode = AsyncMock()
    request_start = AsyncMock()
    monkeypatch.setattr(dashboard, "set_mode", set_mode)
    monkeypatch.setattr(dashboard, "request_start", request_start)

    await dashboard._paper_autostart_if_requested()

    set_mode.assert_not_awaited()
    request_start.assert_not_awaited()


@pytest.mark.asyncio
async def test_paper_autostart_forces_paper_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BTC_PAPER_AUTOSTART", "1")
    monkeypatch.setattr(dashboard, "BOT_MODE", "paper")
    monkeypatch.setattr(dashboard, "_BTC_BOT_AVAILABLE", True)
    set_mode = AsyncMock()
    request_start = AsyncMock(
        return_value=SimpleNamespace(state="running", mode="paper", detail="paper started")
    )
    monkeypatch.setattr(dashboard, "set_mode", set_mode)
    monkeypatch.setattr(dashboard, "request_start", request_start)

    await dashboard._paper_autostart_if_requested()

    set_mode.assert_awaited_once_with("paper")
    request_start.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_paper_autostart_rejects_live_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BTC_PAPER_AUTOSTART", "1")
    monkeypatch.setattr(dashboard, "BOT_MODE", "live")
    set_mode = AsyncMock()
    monkeypatch.setattr(dashboard, "set_mode", set_mode)

    with pytest.raises(RuntimeError, match="requires BOT_MODE=paper"):
        await dashboard._paper_autostart_if_requested()

    set_mode.assert_not_awaited()


def test_task_environment_is_paper_only_and_drops_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setenv("COPILOT_GH_ACCOUNT_example", "test-account-token")
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "test-private-key")

    env = startup._sanitized_environment()

    assert env["BOT_MODE"] == "paper"
    assert env["BTC_PAPER_AUTOSTART"] == "1"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["POLYMARKET_PRIVATE_KEY"] == ""
    assert "GH_TOKEN" not in env
    assert "COPILOT_GH_ACCOUNT_example" not in env


def test_copilot_worktree_is_not_a_durable_install() -> None:
    from pathlib import Path

    assert startup._is_copilot_worktree(
        Path(r"C:\Users\user\.copilot\repos\copilot-worktrees\repo\worktree")
    )
    assert not startup._is_copilot_worktree(
        Path(r"C:\Users\user\AppData\Local\PolymarketCrypto\repository")
    )


def test_current_btc_contract_requires_a_recent_window() -> None:
    now = 1_800_000_123
    current_start = now - now % 300

    assert startup._has_current_btc_contract(
        {"execution_view": f"btc-updown-5m-{current_start}"}, now
    )
    assert startup._has_current_btc_contract(
        {"execution_view": f"btc-updown-15m-{now - now % 900}"}, now
    )
    assert not startup._has_current_btc_contract(
        {"execution_view": "btc-updown-5m-1700000000"}, now
    )
