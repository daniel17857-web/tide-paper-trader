# T-07(R2 改版):AC-11(部位公式,成交價基準)、AC-12(exposure_cap)、
# AC-13(drawdown_halt)+ R2-05 track_terminated
import pytest

import config
from core import risk
from core.models import Plan, PortfolioState, Position, Signal, TrackStatus


def _plan(track="chip", symbol="MTX", entry=20000.0, stop=19900.0, target=20300.0):
    s = Signal(track=track, symbol=symbol, date="2026-07-23",
               kind="zscore", direction="long" if entry > stop else "short")
    return Plan(signal=s, entry_ref=entry, stop=stop, target=target, rr=2.0)


def _state(n_chip=0, n_tech=0, equity=None):
    st = PortfolioState(equity=dict(config.CAPITAL), peak_equity=dict(config.CAPITAL))
    if equity:
        st.equity.update(equity)
    for i in range(n_chip):
        st.positions.append(Position(track="chip", symbol=f"C{i}", timeframe="1d",
                                     kind="zscore", direction="long", size=1.0,
                                     entry_date="d", entry_price=1.0, stop=0.9, target=1.2))
    for i in range(n_tech):
        st.positions.append(Position(track="tech", symbol=f"T{i}", timeframe="1d",
                                     kind="breakout", direction="long", size=1.0,
                                     entry_date="d", entry_price=1.0, stop=0.9, target=1.2))
    return st


def test_ac11_mtx_sizing_at_fill_exact_1pct():
    """R2-01:以實際成交價計部位,停損時恰為資本 1%。"""
    size = risk.position_size_at_fill("chip", "MTX", 20012.0, 19900.0)
    risk_twd = size * config.MTX_POINT_VALUE * (20012.0 - 19900.0)
    target = config.CAPITAL["chip"] * config.RISK_PCT
    assert abs(risk_twd - target) / target < 0.01


def test_ac11_fx_usd_quoted_sizing_at_fill():
    size = risk.position_size_at_fill("tech", "EURUSD", 1.1012, 1.0950)
    risk_usd = size * (1.1012 - 1.0950)
    target = config.CAPITAL["tech"] * config.RISK_PCT
    assert abs(risk_usd - target) / target < 0.01


def test_ac11_usdjpy_sizing_numerator_keeps_stop():
    """USDJPY 分子乘數維持 stop:停損出場換匯後恰為 1%(R2-01 稽核注記)。"""
    entry, stop = 150.4, 149.0
    size = risk.position_size_at_fill("tech", "USDJPY", entry, stop)
    loss_usd = size * (entry - stop) / stop   # 停損時以 stop 匯率換回 USD
    target = config.CAPITAL["tech"] * config.RISK_PCT
    assert abs(loss_usd - target) / target < 0.01


def test_risk_check_issues_qualification_with_estimate():
    order = risk.check(_plan(), _state())
    assert order.status == "approved"
    assert order.size == pytest.approx(
        risk.position_size(_plan()))   # 預估 size(entry_ref 基準)


def test_ac12_exposure_cap_total_3():
    order = risk.check(_plan(), _state(n_chip=1, n_tech=2))
    assert order.status == "rejected"
    assert order.reject_reason == "exposure_cap"


def test_exposure_cap_per_track_2():
    order = risk.check(_plan("chip", "MTX"), _state(n_chip=2))
    assert order.status == "rejected"
    assert order.reject_reason == "exposure_cap_track"


def test_ac13_drawdown_halt_new_only():
    st = _state(n_chip=1, equity={"chip": config.CAPITAL["chip"] * 0.90})
    before = list(st.positions)
    order = risk.check(_plan(), st)
    assert order.status == "rejected"
    assert order.reject_reason == "drawdown_halt"
    assert st.positions == before  # 既有持倉不受影響


def test_halted_status_blocks_even_if_drawdown_recovered():
    """R2-05:狀態機為準——halted 期間即使回撤數字 <10% 仍拒新倉。"""
    st = _state()
    st.track_status["chip"] = TrackStatus(status="halted", halt_date="2026-09-01")
    order = risk.check(_plan(), st)
    assert order.reject_reason == "drawdown_halt"


def test_track_terminated():
    st = _state()
    st.track_status["chip"] = TrackStatus(status="terminated",
                                          halt_date="2026-09-01", resets=2)
    order = risk.check(_plan(), st)
    assert order.status == "rejected"
    assert order.reject_reason == "track_terminated"


def test_drawdown_below_threshold_passes():
    st = _state(equity={"chip": config.CAPITAL["chip"] * 0.905})
    order = risk.check(_plan(), st)
    assert order.status == "approved" and order.size > 0


def test_rejected_plan_stays_rejected():
    plan = _plan()
    plan.status, plan.reject_reason = "rejected", "rr_below_min"
    assert risk.check(plan, _state()).status == "rejected"
