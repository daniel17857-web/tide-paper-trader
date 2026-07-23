# T-06:AC-09(五欄非空 + R:R 計算誤差 < 0.01)、AC-10(R:R < 1.5 → rejected)
import pandas as pd
import pytest

from core.models import Signal
from core.planner import make_plan


def _df(prior_high=101.0):
    """25 根:close=100, high=101, low=95 ⇒ ATR14=6, 結構低=95, 1.5×ATR 停損=91
    ⇒ 停損取較近者 95(風險 5);2R 目標=110。
    prior_high=120 時目標取 min(110,120)=110 ⇒ RR=2.0(accepted)
    prior_high=101 時目標取 min(110,101)=101 ⇒ RR=0.2(rejected)"""
    rows = []
    for i in range(1, 26):
        high = prior_high if i == 18 else 101.0
        rows.append({"timestamp": f"2026-06-{i:02d}", "open": 100.0,
                     "high": high, "low": 95.0, "close": 100.0})
    return pd.DataFrame(rows)


def _signal():
    return Signal(track="chip", symbol="MTX", date="2026-06-25",
                  kind="zscore", direction="long")


def test_ac09_plan_fields_and_rr():
    plan = make_plan(_signal(), _df(prior_high=120.0))
    assert plan.status == "ok"
    assert plan.entry_zone != (0.0, 0.0)
    assert plan.stop == pytest.approx(95.0)
    assert plan.target == pytest.approx(110.0)
    assert plan.invalidation != ""
    expected_rr = (plan.target - plan.entry_ref) / (plan.entry_ref - plan.stop)
    assert plan.rr == pytest.approx(expected_rr, abs=0.01)
    assert plan.rr == pytest.approx(2.0, abs=0.01)


def test_ac10_low_rr_rejected():
    plan = make_plan(_signal(), _df(prior_high=101.0))
    assert plan.status == "rejected"
    assert plan.reject_reason == "rr_below_min"
    assert plan.rr == pytest.approx(0.2, abs=0.01)


def test_suppressed_signal_not_actionable():
    s = Signal(track="chip", symbol="MTX", date="2026-06-25", kind="zscore",
               direction="long", suppressed=True)
    assert make_plan(s, _df()).status == "rejected"


def test_short_plan_mirrors():
    rows = [{"timestamp": f"2026-06-{i:02d}", "open": 100.0, "high": 105.0,
             "low": 80.0 if i == 18 else 99.0, "close": 100.0}
            for i in range(1, 26)]
    s = Signal(track="tech", symbol="EURUSD", date="2026-06-25",
               kind="breakout", direction="short")
    plan = make_plan(s, pd.DataFrame(rows))
    # ATR14 = max(105-99=6,...)=6;1.5ATR 停損=109 vs 結構高 105 → 取 105(較近)
    assert plan.stop == pytest.approx(105.0)
    # 2R 目標 = 100-10=90 vs 前波低 80 → 取 90(較近)⇒ RR=2.0
    assert plan.target == pytest.approx(90.0)
    assert plan.status == "ok"
