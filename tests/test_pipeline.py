# T-11(邏輯部分):advance_broker 的次K進場、cursor 推進、pending 持久化
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
    plan = Plan(signal=s, entry_ref=1.1000, stop=1.0950, target=1.1100, rr=2.0)
    return Order(plan=plan, size=30000.0, status="approved")


def _series():
    return {("EURUSD", "1d"): pd.DataFrame([
        {"timestamp": "2026-06-01", "open": 1.0990, "high": 1.1005,
         "low": 1.0980, "close": 1.1000},
        {"timestamp": "2026-06-02", "open": 1.1010, "high": 1.1050,
         "low": 1.0990, "close": 1.1040},
        {"timestamp": "2026-06-03", "open": 1.1045, "high": 1.1105,
         "low": 1.1030, "close": 1.1090},
    ])}


def _state():
    return PortfolioState(equity=dict(config.CAPITAL), peak_equity=dict(config.CAPITAL))


def test_advance_fills_on_next_bar_after_signal():
    state = _state()
    pending = [_order()]
    cursors = {("EURUSD", "1d"): "2026-06-01"}  # 已處理到訊號K
    closed = pipeline.advance_broker(_series(), state, pending, cursors)
    # 6/2 開盤 1.1010+0.0002 進場,6/3 觸目標出場
    assert len(closed) == 1
    assert closed[0].entry_price == pytest.approx(1.1012)
    assert closed[0].exit_reason == "target"
    assert pending == []  # 已消耗
    assert cursors[("EURUSD", "1d")] == "2026-06-03"


def test_first_seen_series_sets_cursor_without_replay():
    state = _state()
    cursors = {}
    closed = pipeline.advance_broker(_series(), state, [], cursors)
    assert closed == [] and state.positions == []
    assert cursors[("EURUSD", "1d")] == "2026-06-03"


def test_pending_roundtrip(tmp_path):
    path = str(tmp_path / "pending.csv")
    pipeline.save_pending([_order()], path)
    loaded = pipeline.load_pending(path)
    assert len(loaded) == 1
    o = loaded[0]
    assert o.plan.signal.symbol == "EURUSD" and isinstance(o.plan.signal.symbol, str)
    assert o.plan.stop == pytest.approx(1.0950)
    assert o.size == pytest.approx(30000.0)


def test_rerun_idempotent_no_duplicate_orders():
    """同一天重跑:pending 已有同 (symbol, timeframe, 訊號日) 的單不得重複。"""
    state = _state()
    existing = _order()
    sig = existing.plan.signal
    approved, _ = pipeline.generate_orders([sig], _series(), state, [existing])
    assert approved == []


def test_cursor_roundtrip(tmp_path):
    path = str(tmp_path / "cursor.csv")
    pipeline.save_cursors({("EURUSD", "1d"): "2026-06-03",
                           ("MTX", "1d"): "2026-06-02"}, path)
    assert pipeline.load_cursors(path) == {("EURUSD", "1d"): "2026-06-03",
                                           ("MTX", "1d"): "2026-06-02"}
