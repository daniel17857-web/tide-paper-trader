# T-04:AC-05(fixture 逐日比對人工驗算)、AC-06(資料不足回標記不報錯)
import os

import pandas as pd
import pytest

from signals import chip


@pytest.fixture
def chip_df(fixtures_dir):
    return pd.read_csv(os.path.join(fixtures_dir, "chip_case.csv"),
                       dtype={"date": str})


def test_ac05_fixture_replay_matches_manual_answers(chip_df):
    """人工驗算(見 fixture 產生註解):
    day1-20:無有效訊號(z-score 僅回資料不足標記)
    day21:zscore long(外資 z=+2.4)
    day22:resonance long 勝出;zscore long(外資 z≈+2.278)被抑制
    """
    signals = chip.detect_range(chip_df)
    live = [s for s in signals if not s.suppressed and s.direction in ("long", "short")]

    assert [(s.date, s.kind, s.direction) for s in live] == [
        ("2026-06-21", "zscore", "long"),
        ("2026-06-22", "resonance", "long"),
    ]
    z21 = [s for s in live if s.date == "2026-06-21"][0]
    assert z21.meta["institution"] == "foreign"
    assert z21.meta["z"] == pytest.approx(2.4, abs=0.001)

    suppressed = [s for s in signals
                  if s.suppressed and s.direction in ("long", "short")]
    assert [(s.date, s.kind, s.meta.get("suppressed_by")) for s in suppressed] == [
        ("2026-06-22", "zscore", "resonance"),
    ]
    z22 = suppressed[0]
    assert z22.meta["z"] == pytest.approx(2.278, abs=0.005)


def test_ac06_insufficient_data_marker_not_error(chip_df):
    short = chip_df.iloc[:10]
    signals = chip.detect(short)  # 不得 raise
    markers = [s for s in signals if s.meta.get("insufficient_data")]
    assert len(markers) == 2  # 外資、投信各一
    assert all(s.direction == "none" and s.suppressed for s in markers)
    # 不足 20 日不得出現偽 zscore 進場訊號
    assert not any(s.kind == "zscore" and s.direction in ("long", "short")
                   for s in signals)


def test_streak_signals():
    df = pd.DataFrame({
        "date": [f"2026-06-{i:02d}" for i in range(1, 7)],
        "foreign_net": [-100, -200, -150, -300, -250, -50],
        "trust_net": [10, -10, 10, -10, 10, -10],
        "dealer_net": [0] * 6,
    })
    signals = chip.detect(df)
    streaks = [s for s in signals if s.kind == "streak" and not s.suppressed]
    assert len(streaks) == 1 and streaks[0].direction == "short"


def test_symbol_and_date_are_strings(chip_df):
    for s in chip.detect(chip_df):
        assert isinstance(s.symbol, str) and isinstance(s.date, str)
