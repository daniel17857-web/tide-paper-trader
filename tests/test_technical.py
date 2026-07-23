# T-05:AC-07(fixture 比對)、AC-08(多訊號並發抑制:突破 > 動能 > 回調)
import os

import pandas as pd
import pytest

from signals import technical


@pytest.fixture
def tech_df(fixtures_dir):
    return pd.read_csv(os.path.join(fixtures_dir, "tech_case.csv"),
                       dtype={"timestamp": str})


def test_ac07_fixture_replay_matches_manual_answers(tech_df):
    """人工驗算:bar1-24 全平無訊號;bar25 大陽棒三訊號並發,
    breakout 勝出,momentum、pullback 被抑制。"""
    signals = technical.detect_range(tech_df, "EURUSD", "1d")
    assert all(s.date == "2026-06-25" for s in signals)  # 前 24 根無任何訊號

    live = [s for s in signals if not s.suppressed]
    assert [(s.kind, s.direction) for s in live] == [("breakout", "long")]
    assert live[0].meta["level"] == pytest.approx(1.1000)

    suppressed = {s.kind: s.meta["suppressed_by"] for s in signals if s.suppressed}
    assert suppressed == {"momentum": "breakout", "pullback": "breakout"}


def test_ac08_priority_momentum_over_pullback():
    sigs = [
        technical.Signal(track="tech", symbol="EURUSD", date="d", kind="pullback",
                         direction="long", timeframe="1d"),
        technical.Signal(track="tech", symbol="EURUSD", date="d", kind="momentum",
                         direction="long", timeframe="1d"),
    ]
    out = technical._suppress(sigs)
    live = [s for s in out if not s.suppressed]
    assert len(live) == 1 and live[0].kind == "momentum"
    assert [s.kind for s in out if s.suppressed] == ["pullback"]


def test_short_breakout():
    rows = [{"timestamp": f"2026-06-{i:02d}", "open": 1.2, "high": 1.2,
             "low": 1.2, "close": 1.2} for i in range(1, 25)]
    rows.append({"timestamp": "2026-06-25", "open": 1.2, "high": 1.2005,
                 "low": 1.15, "close": 1.16})
    df = pd.DataFrame(rows)
    live = [s for s in technical.detect(df, "GBPUSD", "1h") if not s.suppressed]
    assert len(live) == 1
    assert live[0].kind == "breakout" and live[0].direction == "short"
    assert live[0].timeframe == "1h"


def test_insufficient_bars_no_signal():
    rows = [{"timestamp": f"2026-06-{i:02d}", "open": 1.1, "high": 1.2,
             "low": 1.0, "close": 1.15} for i in range(1, 11)]
    assert technical.detect(pd.DataFrame(rows), "EURUSD") == []
