# R2-05(A 案):熔斷狀態機——事件落地、冷卻解除+peak 重置、第 3 次熔斷終止
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import config
import run_pipeline as pipeline
from core import broker
from core.models import PortfolioState, Position, TrackStatus


def _state(chip_equity):
    st = PortfolioState(equity=dict(config.CAPITAL), peak_equity=dict(config.CAPITAL))
    st.equity["chip"] = chip_equity
    return st


@pytest.fixture
def halt_log(monkeypatch):
    rows = []
    monkeypatch.setattr(broker, "append_halt", lambda row, path=None: rows.append(row))
    return rows


def test_halt_event_recorded_once(halt_log, monkeypatch):
    monkeypatch.setattr(pipeline, "_track_trading_dates", lambda track: [])
    st = _state(config.CAPITAL["chip"] * 0.89)   # 回撤 11%
    pipeline.update_halt_state(st, "2026-11-01 16:30")
    ts = st.status_of("chip")
    assert ts.status == "halted" and ts.halt_date == "2026-11-01"
    assert len(halt_log) == 1 and halt_log[0]["track"] == "chip"
    assert halt_log[0]["reset_no"] == 0
    # 再跑一次:已在熔斷狀態,不重複記錄
    pipeline.update_halt_state(st, "2026-11-02 16:30")
    assert len(halt_log) == 1


def test_cooldown_release_resets_peak(halt_log, monkeypatch):
    dates = [f"2026-11-{d:02d}" for d in range(2, 28)]  # halt 後 26 個交易日
    monkeypatch.setattr(pipeline, "_track_trading_dates", lambda track: dates)
    st = _state(config.CAPITAL["chip"] * 0.89)
    st.track_status["chip"] = TrackStatus(status="halted", halt_date="2026-11-01")
    pipeline.update_halt_state(st, "2026-12-01 16:30")
    ts = st.status_of("chip")
    assert ts.status == "active" and ts.resets == 1
    assert st.peak_equity["chip"] == pytest.approx(st.equity["chip"])  # peak 重置


def test_cooldown_requires_no_positions(halt_log, monkeypatch):
    dates = [f"2026-11-{d:02d}" for d in range(2, 28)]
    monkeypatch.setattr(pipeline, "_track_trading_dates", lambda track: dates)
    st = _state(config.CAPITAL["chip"] * 0.89)
    st.track_status["chip"] = TrackStatus(status="halted", halt_date="2026-11-01")
    st.positions.append(Position(track="chip", symbol="MTX", timeframe="1d",
                                 kind="zscore", direction="long", size=1.0,
                                 entry_date="d", entry_price=1.0, stop=0.9,
                                 target=1.2))
    pipeline.update_halt_state(st, "2026-12-01 16:30")
    assert st.status_of("chip").status == "halted"  # 有持倉 → 不解除(D-01)


def test_cooldown_not_elapsed_stays_halted(halt_log, monkeypatch):
    dates = [f"2026-11-{d:02d}" for d in range(2, 10)]  # 僅 8 個交易日
    monkeypatch.setattr(pipeline, "_track_trading_dates", lambda track: dates)
    st = _state(config.CAPITAL["chip"] * 0.89)
    st.track_status["chip"] = TrackStatus(status="halted", halt_date="2026-11-01")
    pipeline.update_halt_state(st, "2026-11-12 16:30")
    assert st.status_of("chip").status == "halted"


def test_third_halt_terminates(halt_log, monkeypatch):
    monkeypatch.setattr(pipeline, "_track_trading_dates", lambda track: [])
    st = _state(config.CAPITAL["chip"] * 0.89)
    st.track_status["chip"] = TrackStatus(status="active", resets=config.HALT_MAX_RESETS)
    pipeline.update_halt_state(st, "2027-01-05 16:30")
    assert st.status_of("chip").status == "terminated"
    assert len(halt_log) == 1 and halt_log[0]["reset_no"] == config.HALT_MAX_RESETS
