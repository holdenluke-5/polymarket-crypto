"""Strategy Lab replay (polymarket_bot.strategy_lab): hand-built windows, no network."""
from __future__ import annotations

import pytest

from polymarket_bot import strategy_lab as lab


def _window(i: int, up: float, ticks: list[lab.Tick], block: int = 1) -> lab.Window:
    return lab.Window(("5m", 1_000_000 + 300 * i), up, ticks, block)


def _tick(rem, fair, ua, da, ub=None, db=None, size=500.0):
    return lab.Tick(rem, fair, ua, da, ub, db, size, size)


def test_settle_pays_one_entry_fee_and_settles_at_the_outcome() -> None:
    w = _window(1, 1.0, [_tick(200, 0.80, 0.74, 0.27), _tick(150, 0.85, 0.80, 0.21)])
    (t,) = lab.simulate([w], lab.Rules(), {1: None})
    fee = 5 * 0.07 * 0.74 * 0.26
    assert t.exit == "SETTLE" and t.entry == 0.74
    assert t.net == pytest.approx(5 * 0.26 - fee)


def test_scalp_sells_at_the_bid_and_pays_a_second_fee() -> None:
    w = _window(1, 0.0, [
        _tick(200, 0.80, 0.74, 0.27, 0.73, 0.26),
        _tick(180, 0.90, 0.85, 0.16, 0.84, 0.15),  # +10c at the bid: above the 10% target
    ])
    (t,) = lab.simulate([w], lab.Rules(exit_style="scalp"), {1: None})
    assert t.exit == "TARGET"
    assert t.net == pytest.approx(5 * (0.84 - 0.74) - 5 * 0.07 * (0.74 * 0.26 + 0.84 * 0.16))


def test_block_zero_only_trains_and_fills_are_capped_by_the_book() -> None:
    ticks = [_tick(200, 0.80, 0.74, 0.27, size=8.0)]
    trades = lab.simulate([_window(0, 1.0, ticks, block=0), _window(1, 1.0, ticks)],
                          lab.Rules(shares=50.0), {1: None})
    assert len(trades) == 1 and trades[0].shares == 8.0 and trades[0].capped


def test_learned_rules_need_a_walk_forward_fit() -> None:
    w = _window(1, 1.0, [_tick(200, 0.80, 0.74, 0.27)])
    assert lab.simulate([w], lab.Rules(learned=True), {1: None}) == []
    assert len(lab.simulate([w], lab.Rules(learned=True), {1: (1.0, 0.0)})) == 1


def test_score_flags_results_beyond_luck() -> None:
    def trade(net):
        return lab.Trade(1, "Up", 0.7, 5, net, net, "SETTLE", False)
    assert lab.score([trade(0.5)] * 40)["verdict"] == "ahead"
    assert lab.score([trade(-0.5)] * 40)["verdict"] == "behind"
    assert lab.score([trade(1.0), trade(-1.0)] * 20)["verdict"] == "luck"
    assert lab.score([trade(1.0)] * 5)["verdict"] == "too_early"
    assert lab.score([trade(1.0), trade(-3.0), trade(1.0)])["worst_drawdown_usd"] == -3.0



def test_compare_learning_scores_both_sides_on_the_same_windows(monkeypatch) -> None:
    # Up wins; the raw model (80%) buys Up at 74c, while a market-only fit
    # (a=0, b=1) sees no edge and stays out.
    ticks = [_tick(200, 0.80, 0.74, 0.27)]
    windows = [_window(i, 1.0, ticks, block=i * lab.BLOCKS // 10) for i in range(10)]
    monkeypatch.setattr(lab, "load_history", lambda db: windows)
    monkeypatch.setattr(lab, "_learned_fits", lambda ws: {k: (0.0, 1.0) for k in range(lab.BLOCKS)})
    out = lab.compare_learning("unused.db", lab.Rules())
    assert out["ok"] and out["windows"] == 8
    assert out["raw_n"] == 8 and out["learned_n"] == 0
    assert out["raw_usd"] == pytest.approx(8 * (5 * 0.26 - 5 * 0.07 * 0.74 * 0.26))
    monkeypatch.setattr(lab, "load_history", lambda db: windows[:3])
    assert lab.compare_learning("unused.db", lab.Rules())["ok"] is False
