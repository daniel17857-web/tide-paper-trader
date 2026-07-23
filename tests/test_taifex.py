# T-02:AC-01(累積+去重)、AC-02(無新資料不寫空列、正常結束)
import pandas as pd

from adapters import taifex

OHLC_ROWS = [
    # 週契約(月份欄 7 碼)應被排除
    {"Date": "20260722", "Contract": "TX", "ContractMonth(Week)": "202607W5",
     "TradingSession": "一般", "Open": "1", "High": "1", "Low": "1",
     "Last": "1", "Volume": "1"},
    # 次月契約(排序後不取)
    {"Date": "20260722", "Contract": "TX", "ContractMonth(Week)": "202609",
     "TradingSession": "一般", "Open": "23500", "High": "23600",
     "Low": "23400", "Last": "23550", "Volume": "1000"},
    # 最近月一般時段 → 應取這筆
    {"Date": "20260722", "Contract": "TX", "ContractMonth(Week)": "202608",
     "TradingSession": "一般", "Open": "23400", "High": "23500",
     "Low": "23300", "Last": "23450", "Volume": "90000"},
    # 盤後時段應排除
    {"Date": "20260722", "Contract": "TX", "ContractMonth(Week)": "202608",
     "TradingSession": "盤後", "Open": "0", "High": "0", "Low": "0",
     "Last": "0", "Volume": "0"},
    {"Date": "20260722", "Contract": "MTX", "ContractMonth(Week)": "202608",
     "TradingSession": "一般", "Open": "9", "High": "9", "Low": "9",
     "Last": "9", "Volume": "9"},
]

INST_ROWS = [
    {"Date": "20260722", "ContractCode": "臺股期貨", "Item": "外資及陸資",
     "OpenInterest(Net)": "12345"},
    {"Date": "20260722", "ContractCode": "臺股期貨", "Item": "投信",
     "OpenInterest(Net)": "-678"},
    {"Date": "20260722", "ContractCode": "臺股期貨", "Item": "自營商",
     "OpenInterest(Net)": "90"},
    {"Date": "20260722", "ContractCode": "小型臺指", "Item": "外資",
     "OpenInterest(Net)": "999999"},
]


def test_parse_ohlc_picks_near_month_regular_session():
    row = taifex.parse_ohlc(OHLC_ROWS)
    assert row == {"date": "2026-07-22", "open": 23400.0, "high": 23500.0,
                   "low": 23300.0, "close": 23450.0, "volume": 90000.0}


def test_parse_institutional_tx_only():
    row = taifex.parse_institutional(INST_ROWS)
    assert row["foreign_net"] == 12345.0
    assert row["trust_net"] == -678.0
    assert row["dealer_net"] == 90.0
    assert row["date"] == "2026-07-22"


def test_ac01_append_and_dedup(tmp_path, monkeypatch):
    path = str(tmp_path / "hist.csv")
    monkeypatch.setattr(taifex, "_get_json",
                        lambda url, timeout=30: OHLC_ROWS if "Report" in url else INST_ROWS)
    hist = taifex.fetch_daily(path)
    assert len(hist) == 1
    assert list(hist.columns) == taifex.HISTORY_COLUMNS
    assert hist["date"].iloc[0] == "2026-07-22"
    # 同日重跑:去重,不新增
    hist2 = taifex.fetch_daily(path)
    assert len(hist2) == 1
    saved = pd.read_csv(path, dtype={"date": str})
    assert saved["date"].is_unique


def test_ac02_no_data_writes_nothing(tmp_path, monkeypatch):
    path = str(tmp_path / "hist.csv")
    monkeypatch.setattr(taifex, "_get_json", lambda url, timeout=30: [])
    hist = taifex.fetch_daily(path)  # 不得 raise(exit code 0 由管線層保證)
    assert len(hist) == 0
    assert not (tmp_path / "hist.csv").exists()  # 不寫空列


def test_ohlc_inst_date_mismatch_not_written(tmp_path, monkeypatch):
    inst_stale = [dict(r, Date="20260721") for r in INST_ROWS]
    path = str(tmp_path / "hist.csv")
    monkeypatch.setattr(taifex, "_get_json",
                        lambda url, timeout=30: OHLC_ROWS if "Report" in url else inst_stale)
    hist = taifex.fetch_daily(path)
    assert len(hist) == 0
