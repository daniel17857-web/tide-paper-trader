# T-07:AC-11(部位公式誤差 < 1%)、AC-12(exposure_cap)、AC-13(drawdown_halt)
import pytest

import config
from core import risk
from core.models import Plan, PortfolioState, Position, Signal


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


def test_ac11_mtx_sizing_exact_1pct():
    plan = _plan("chip", "MTX", entry=20000.0, stop=19900.0)
    size = risk.position_size(plan)
    risk_twd = size * config.MTX_POINT_VALUE * (20000.0 - 19900.0)
    target = config.CAPITAL["chip"] * config.RISK_PCT
    assert abs(risk_twd - target) / target < 0.01
    assert size == pytest.approx(2.0)  # 10,000 / (50×100)


def test_ac11_fx_usd_quoted_sizing():
    plan = _plan("tech", "EURUSD", entry=1.1000, stop=1.0900)
    size = risk.position_size(plan)
    risk_usd = size * (1.1000 - 1.0900)
    target = config.CAPITAL["tech"] * config.RISK_PCT
    assert abs(risk_usd - target) / target < 0.01
    assert size == pytest.approx(30000.0)  # 300 / 0.01


def test_ac11_usdjpy_sizing_converted_at_stop():
    plan = _plan("tech", "USDJPY", entry=150.0, stop=149.0)
    size = risk.position_size(plan)
    # 停損時損失(JPY)= size × 1;換回 USD 於停損價 149 ⇒ 恰為 300 USD
    loss_usd = size * (150.0 - 149.0) / 149.0
    target = config.CAPITAL["tech"] * config.RISK_PCT
    assert abs(loss_usd - target) / target < 0.01


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


def test_drawdown_below_threshold_passes():
    st = _state(equity={"chip": config.CAPITAL["chip"] * 0.905})
    order = risk.check(_plan(), st)
    assert order.status == "approved" and order.size > 0


def test_rejected_plan_stays_rejected():
    plan = _plan()
    plan.status, plan.reject_reason = "rejected", "rr_below_min"
    assert risk.check(plan, _state()).status == "rejected"
