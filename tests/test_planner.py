# T-06(R2 改版):AC-09/AC-10 + R2-02 stop_too_tight 預檢 + R2-04 週期化失效文字
# 尺度採 MTX 量級(滑價 2 點;R2-02 下停損距離須 ≥ 20 點)
import pandas as pd
import pytest

import config
from core.models import Signal
from core.planner import make_plan


def _df(prior_high=10100.0):
    """25 根:close=10000, high=10100, low=9500 ⇒ ATR14=600, 結構低=9500,
    1.5×ATR 停損=9100 ⇒ 停損取較近者 9500(風險 500);2R 目標=11000。
    prior_high=12000 時目標取 min(11000,12000)=11000 ⇒ RR=2.0(accepted)
    prior_high=10100 時目標取 min(11000,10100)=10100 ⇒ RR=0.2(rejected)"""
    rows = []
    for i in range(1, 26):
        high = prior_high if i == 18 else 10100.0
        rows.append({"timestamp": f"2026-06-{i:02d}", "open": 10000.0,
                     "high": high, "low": 9500.0, "close": 10000.0})
    return pd.DataFrame(rows)


def _signal():
    return Signal(track="chip", symbol="MTX", date="2026-06-25",
                  kind="zscore", direction="long")


def test_ac09_plan_fields_and_rr():
    plan = make_plan(_signal(), _df(prior_high=12000.0))
    assert plan.status == "ok"
    assert plan.entry_zone != (0.0, 0.0)
    assert plan.stop == pytest.approx(9500.0)
    assert plan.target == pytest.approx(11000.0)
    assert plan.invalidation != ""
    expected_rr = (plan.target - plan.entry_ref) / (plan.entry_ref - plan.stop)
    assert plan.rr == pytest.approx(expected_rr, abs=0.01)
    assert plan.rr == pytest.approx(2.0, abs=0.01)


def test_ac10_low_rr_rejected():
    plan = make_plan(_signal(), _df(prior_high=10100.0))
    assert plan.status == "rejected"
    assert plan.reject_reason == "rr_below_min"
    assert plan.rr == pytest.approx(0.2, abs=0.01)


def test_r2_02_stop_too_tight_precheck():
    """停損距離 < MIN_STOP_SLIP_RATIO×滑價(MTX:20 點)→ rejected。
    全平盤 + 微幅 ATR:close 10000、high 10004、low 9995 ⇒ ATR=9,
    1.5×ATR 停損 = 9986.5,結構低 9995 ⇒ 停損 9995,距離 5 < 20。"""
    rows = [{"timestamp": f"2026-06-{i:02d}", "open": 10000.0, "high": 10004.0,
             "low": 9995.0, "close": 10000.0} for i in range(1, 26)]
    plan = make_plan(_signal(), pd.DataFrame(rows))
    assert plan.status == "rejected"
    assert plan.reject_reason == "stop_too_tight"


def test_r2_04_invalidation_text_per_timeframe():
    plan_1d = make_plan(_signal(), _df(prior_high=12000.0))
    assert str(config.MAX_HOLD_BARS["1d"]) in plan_1d.invalidation
    s_1h = Signal(track="tech", symbol="EURUSD", date="2026-06-25T10:00:00",
                  kind="breakout", direction="long", timeframe="1h")
    rows = [{"timestamp": f"t{i:02d}", "open": 1.1, "high": 1.11 if i != 18 else 1.2,
             "low": 1.05, "close": 1.1} for i in range(1, 26)]
    plan_1h = make_plan(s_1h, pd.DataFrame(rows))
    assert plan_1h.status == "ok"
    assert str(config.MAX_HOLD_BARS["1h"]) in plan_1h.invalidation


def test_suppressed_signal_not_actionable():
    s = Signal(track="chip", symbol="MTX", date="2026-06-25", kind="zscore",
               direction="long", suppressed=True)
    assert make_plan(s, _df()).status == "rejected"


def test_short_plan_mirrors():
    rows = [{"timestamp": f"2026-06-{i:02d}", "open": 10000.0, "high": 10500.0,
             "low": 8000.0 if i == 18 else 9900.0, "close": 10000.0}
            for i in range(1, 26)]
    s = Signal(track="tech", symbol="EURUSD", date="2026-06-25",
               kind="breakout", direction="short")
    plan = make_plan(s, pd.DataFrame(rows))
    # ATR14=600;1.5ATR 停損=10900 vs 結構高 10500 → 取 10500(較近)
    assert plan.stop == pytest.approx(10500.0)
    # 2R 目標 = 10000-1000=9000 vs 前波低 8000 → 取 9000(較近)⇒ RR=2.0
    assert plan.target == pytest.approx(9000.0)
    assert plan.status == "ok"
