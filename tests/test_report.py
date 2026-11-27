# T-10(R2 改版):報告頁 sanity——風控狀態、風控拒絕註記、封存區塊、免責聲明
import os

import pandas as pd

import config
from core.models import LEDGER_COLUMNS, PortfolioState, Position, Signal, TrackStatus
from report.build_report import build_html, write_report


def _state():
    st = PortfolioState(equity=dict(config.CAPITAL), peak_equity=dict(config.CAPITAL))
    st.positions = [Position(track="tech", symbol="EURUSD", timeframe="1d",
                             kind="breakout", direction="long", size=30000.0,
                             entry_date="2026-07-22", entry_price=1.1012,
                             stop=1.0950, target=1.1150)]
    return st


def _signals():
    return [
        Signal(track="chip", symbol="MTX", date="2026-07-23", kind="zscore",
               direction="long"),
        Signal(track="tech", symbol="EURUSD", date="2026-07-23", kind="momentum",
               direction="long", suppressed=True,
               meta={"suppressed_by": "breakout"}),
        Signal(track="tech", symbol="GBPUSD", date="2026-07-23", kind="pullback",
               direction="short"),
    ]


def _metrics():
    return {"chip": {"track": "chip", "trades": 0, "win_rate": None,
                     "avg_r": None, "max_drawdown": None, "profit_factor": None},
            "tech": {"track": "tech", "trades": 2, "win_rate": 0.5,
                     "avg_r": 0.375, "max_drawdown": 0.01, "profit_factor": 2.0}}


def test_build_html_contents():
    state = _state()
    state.track_status["tech"] = TrackStatus(status="halted",
                                             halt_date="2026-09-01", resets=1)
    html = build_html("2026-07-23 16:30", _signals(), state, _metrics(),
                      outcomes={("GBPUSD", "1d", "pullback"): "drawdown_halt",
                                ("MTX", "1d", "zscore"): "approved"})
    assert 'name="viewport"' in html
    assert "純模擬交易" in html
    assert "z-score 異常" in html and "被抑制" in html
    assert "熔斷冷卻中" in html                       # R2-05 風控狀態列
    assert "風控拒絕(drawdown_halt)" in html        # R2-08 訊號註記
    assert "有效(已核准)" in html
    assert "lang=\"zh-Hant\"" in html
    assert "與第一輪不可比較" in html                 # R2-06


def test_write_report(tmp_path):
    path = str(tmp_path / "index.html")
    ledger = pd.DataFrame(columns=LEDGER_COLUMNS)
    out = write_report("2026-07-23 16:30", _signals(), _state(),
                       ledger=ledger, path=path)
    assert os.path.exists(out)
    with open(out, encoding="utf-8") as f:
        assert "潮汐模擬倉" in f.read()
