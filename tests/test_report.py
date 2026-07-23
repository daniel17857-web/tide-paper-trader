# T-10:報告頁自動化 sanity(AC-18 的 390px 版面另需人工檢核)
import os

import pandas as pd

import config
from core.models import LEDGER_COLUMNS, PortfolioState, Position, Signal
from report.build_report import build_html, write_report


def _state():
    st = PortfolioState(equity=dict(config.CAPITAL), peak_equity=dict(config.CAPITAL))
    st.positions = [Position(track="tech", symbol="EURUSD", timeframe="1d",
                             kind="breakout", direction="long", size=30000.0,
                             entry_date="2026-07-22", entry_price=1.1012,
                             stop=1.0950, target=1.1100)]
    return st


def _signals():
    return [
        Signal(track="chip", symbol="MTX", date="2026-07-23", kind="zscore",
               direction="long"),
        Signal(track="tech", symbol="EURUSD", date="2026-07-23", kind="momentum",
               direction="long", suppressed=True,
               meta={"suppressed_by": "breakout"}),
    ]


def test_build_html_contents():
    html = build_html("2026-07-23 16:30", _signals(),  _state(),
                      {"chip": {"track": "chip", "trades": 0, "win_rate": None,
                                "avg_r": None, "max_drawdown": None,
                                "profit_factor": None},
                       "tech": {"track": "tech", "trades": 2, "win_rate": 0.5,
                                "avg_r": 0.375, "max_drawdown": 0.01,
                                "profit_factor": 2.0}})
    assert 'name="viewport"' in html          # 手機優先
    assert "純模擬交易" in html                # 固定免責聲明(交接風險提示)
    assert "z-score 異常" in html and "被抑制" in html
    assert "EURUSD" in html
    assert "lang=\"zh-Hant\"" in html          # 台灣正體中文


def test_write_report(tmp_path):
    path = str(tmp_path / "index.html")
    ledger = pd.DataFrame(columns=LEDGER_COLUMNS)
    out = write_report("2026-07-23 16:30", _signals(), _state(),
                       ledger=ledger, path=path)
    assert os.path.exists(out)
    with open(out, encoding="utf-8") as f:
        assert "潮汐模擬倉" in f.read()
