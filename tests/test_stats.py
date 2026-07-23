# T-09:AC-16(10 筆已知交易 fixture,四指標與人工驗算誤差 < 0.1%)
import os

import pandas as pd
import pytest

from core import stats


@pytest.fixture
def ledger(fixtures_dir):
    return pd.read_csv(os.path.join(fixtures_dir, "ledger_10trades.csv"),
                       dtype={"symbol": str})


def test_ac16_chip_metrics(ledger):
    """人工驗算(chip 6 筆:+10000,+5000,-5000,-10000,+20000,-2500):
    勝率 3/6=0.5;平均R=(1+0.5-1-1+2-0.25)/6=0.208333;
    獲利因子=35000/17500=2.0;
    MDD:權益 101,101.5,101,100,102,101.75 萬 → 峰 101.5 谷 100 → 15000/1015000。"""
    m = stats.track_metrics(ledger, "chip")
    assert m["trades"] == 6
    assert m["win_rate"] == pytest.approx(0.5, rel=1e-3)
    assert m["avg_r"] == pytest.approx(1.25 / 6, rel=1e-3)
    assert m["profit_factor"] == pytest.approx(2.0, rel=1e-3)
    assert m["max_drawdown"] == pytest.approx(15000 / 1015000, rel=1e-3)


def test_ac16_tech_metrics(ledger):
    """人工驗算(tech 4 筆:+300,-300,+600,-150):
    勝率 0.5;平均R=(1-1+2-0.5)/4=0.375;獲利因子=900/450=2.0;
    MDD:30300,30000,30600,30450 → 300/30300。"""
    m = stats.track_metrics(ledger, "tech")
    assert m["trades"] == 4
    assert m["win_rate"] == pytest.approx(0.5, rel=1e-3)
    assert m["avg_r"] == pytest.approx(0.375, rel=1e-3)
    assert m["profit_factor"] == pytest.approx(2.0, rel=1e-3)
    assert m["max_drawdown"] == pytest.approx(300 / 30300, rel=1e-3)


def test_summarize_both_tracks(ledger):
    summary = stats.summarize(ledger)
    assert set(summary) == {"chip", "tech"}


def test_empty_ledger_no_error():
    import pandas as pd
    from core.models import LEDGER_COLUMNS
    m = stats.track_metrics(pd.DataFrame(columns=LEDGER_COLUMNS), "chip")
    assert m["trades"] == 0 and m["win_rate"] is None
