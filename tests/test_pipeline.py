# T-11(R2 邏輯):advance_broker 次K進場、cursor 推進、pending 持久化、冪等
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import config
import run_pipeline as pipeline
from core.models import Order, Plan, PortfolioState, Signal


def _order():
    s = Signal(track="tech", symbol="EURUSD", date="2026-06-01", kind="breakout",
               direction="long")
    plan = Plan(signal=s, entry_ref=1.1000, stop=1.0950, target=1.1150, rr=3.0)
    return Order(plan=plan, size=0.0, status="approved")


def _series():
    return {("EURUSD", "1d"): pd.DataFrame([
        {"timestamp": "2026-06-01", "open": 1.0990, "high": 1.1005,
         "low": 1.0980, "close": 1.1000},
        {"timestamp": "2026-06-02", "open": 1.1010, "high": 1.1050,
         "low": 1.0990, "close": 1.1040},
        {"timestamp": "2026-06-03", "open": 1.1045, "high": 1.1160,
         "low": 1.1030, "close": 1.1120},
    ])}


def _state():
    return PortfolioState(equity=dict(config.CAPITAL), peak_equity=dict(config.CAPITAL))


def test_advance_fills_on_next_bar_after_signal():
    state = _state()
    pending = [_order()]
    cursors = {("EURUSD", "1d"): "2026-06-01"}  # 已處理到訊號K
    closed, rejects = pipeline.advance_broker(_series(), state, pending, cursors)
    # 6/2 開盤 1.1010+0.0002 進場(成交時定部位),6/3 觸目標出場
    assert len(closed) == 1 and rejects == []
    assert closed[0].entry_price == pytest.approx(1.1012)
    assert closed[0].size == pytest.approx(300.0 / 0.0062)
    assert closed[0].exit_reason == "target"
    assert pending == []  # 已消耗
    assert cursors[("EURUSD", "1d")] == "2026-06-03"


def test_first_seen_series_sets_cursor_without_replay():
    state = _state()
    cursors = {}
    closed, rejects = pipeline.advance_broker(_series(), state, [], cursors)
    assert closed == [] and rejects == [] and state.positions == []
    assert cursors[("EURUSD", "1d")] == "2026-06-03"


def test_pending_roundtrip(tmp_path):
    path = str(tmp_path / "pending.csv")
    pipeline.save_pending([_order()], path)
    loaded = pipeline.load_pending(path)
    assert len(loaded) == 1
    o = loaded[0]
    assert o.plan.signal.symbol == "EURUSD" and isinstance(o.plan.signal.symbol, str)
    assert o.plan.stop == pytest.approx(1.0950)


def test_rerun_idempotent_no_duplicate_orders():
    state = _state()
    existing = _order()
    sig = existing.plan.signal
    approved, rejects, outcomes = pipeline.generate_orders(
        [sig], _series(), state, [existing])
    assert approved == []


def test_generate_orders_records_rejects_and_outcomes():
    """風控拒單要落 rejects 與 outcomes(R2-08)。"""
    from core.models import TrackStatus
    state = _state()
    state.track_status["tech"] = TrackStatus(status="terminated", resets=2)
    sig = Signal(track="tech", symbol="EURUSD", date="2026-06-25",
                 kind="breakout", direction="long")
    rows = [{"timestamp": f"2026-06-{i:02d}", "open": 1.1, "high": 1.2 if i == 18 else 1.11,
             "low": 1.05, "close": 1.1} for i in range(1, 26)]
    series = {("EURUSD", "1d"): pd.DataFrame(rows)}
    approved, rejects, outcomes = pipeline.generate_orders([sig], series, state)
    assert approved == []
    assert rejects[0]["reason"] == "track_terminated"
    assert outcomes[("EURUSD", "1d", "breakout")] == "track_terminated"


def test_cursor_roundtrip(tmp_path):
    path = str(tmp_path / "cursor.csv")
    pipeline.save_cursors({("EURUSD", "1d"): "2026-06-03",
                           ("MTX", "1d"): "2026-06-02"}, path)
    assert pipeline.load_cursors(path) == {("EURUSD", "1d"): "2026-06-03",
                                           ("MTX", "1d"): "2026-06-02"}
