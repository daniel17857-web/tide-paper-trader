# T-02:AC-01(累積+去重)、AC-02(無新資料不寫空列、正常結束)
# T-12:日行情端點 CSV 後援(_get_rows)
import logging
import os

import pandas as pd
import pytest
import requests

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
    monkeypatch.setattr(
        taifex, "_get_rows",
        lambda url, csv_map=None, timeout=30: OHLC_ROWS if "Report" in url else INST_ROWS)
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
    monkeypatch.setattr(taifex, "_get_rows", lambda url, csv_map=None, timeout=30: [])
    hist = taifex.fetch_daily(path)  # 不得 raise(exit code 0 由管線層保證)
    assert len(hist) == 0
    assert not (tmp_path / "hist.csv").exists()  # 不寫空列


def test_ohlc_inst_date_mismatch_not_written(tmp_path, monkeypatch):
    inst_stale = [dict(r, Date="20260721") for r in INST_ROWS]
    path = str(tmp_path / "hist.csv")
    monkeypatch.setattr(
        taifex, "_get_rows",
        lambda url, csv_map=None, timeout=30: OHLC_ROWS if "Report" in url else inst_stale)
    hist = taifex.fetch_daily(path)
    assert len(hist) == 0


# ── T-12:日行情端點 CSV 後援 ──────────────────────────────────────────────
FIXTURE_CSV = os.path.join(os.path.dirname(__file__), "fixtures", "taifex_ohlc_20261014.csv")
EXPECTED_1014 = {"date": "2026-10-14", "open": 48789.0, "high": 49200.0,
                 "low": 48650.0, "close": 48792.0, "volume": 44607.0}
INST_ROWS_1014 = [dict(r, Date="20261014") for r in INST_ROWS]


def _csv_bytes() -> bytes:
    with open(FIXTURE_CSV, "rb") as f:
        return f.read()


def _csv_without_session_column() -> bytes:
    text = _csv_bytes().decode("utf-8-sig").replace("交易時段", "交易時間", 1)
    return text.encode("utf-8")


def _json_decode_error() -> Exception:
    return requests.exceptions.JSONDecodeError("Expecting value", "", 0)


class _FakeResp:
    def __init__(self, json_value=None, json_exc=None, content=b""):
        self._json_value, self._json_exc, self.content = json_value, json_exc, content

    def raise_for_status(self):
        pass

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_value


def _csv_resp(content: bytes | None = None) -> _FakeResp:
    return _FakeResp(json_exc=_json_decode_error(),
                     content=_csv_bytes() if content is None else content)


def _patch_get(monkeypatch, by_url: dict):
    monkeypatch.setattr(taifex.requests, "get",
                        lambda url, timeout=30, headers=None: by_url[url])


def test_fixture_has_utf8_bom():
    assert _csv_bytes().startswith(b"\xef\xbb\xbf")


def test_get_rows_csv_fallback_ohlc(monkeypatch, caplog):
    _patch_get(monkeypatch, {taifex.OHLC_ENDPOINT: _csv_resp()})
    with caplog.at_level(logging.WARNING, logger=taifex.log.name):
        rows = taifex._get_rows(taifex.OHLC_ENDPOINT, taifex._OHLC_CSV_MAP)
    assert taifex.parse_ohlc(rows) == EXPECTED_1014
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "CSV" in caplog.records[0].getMessage()


def test_csv_rows_keep_only_mapped_columns_as_str(monkeypatch):
    _patch_get(monkeypatch, {taifex.OHLC_ENDPOINT: _csv_resp()})
    rows = taifex._get_rows(taifex.OHLC_ENDPOINT, taifex._OHLC_CSV_MAP)
    assert len(rows) == 6
    for r in rows:
        assert set(r) == set(taifex._OHLC_CSV_MAP.values())
        assert all(isinstance(v, str) for v in r.values())
    assert rows[0]["Date"] == "20261014" and rows[0]["ContractMonth(Week)"] == "202610"


def test_csv_header_and_values_are_stripped(monkeypatch):
    csv_map = {"日期": "Date", "契約代號": "Contract"}
    content = " 日期 , 契約代號 \r\n 20261014 , TX \r\n".encode("utf-8")
    _patch_get(monkeypatch, {"u": _csv_resp(content)})
    assert taifex._get_rows("u", csv_map) == [{"Date": "20261014", "Contract": "TX"}]


def test_fetch_daily_with_csv_ohlc_appends_row(tmp_path, monkeypatch):
    path = str(tmp_path / "hist.csv")
    _patch_get(monkeypatch, {taifex.OHLC_ENDPOINT: _csv_resp(),
                             taifex.INST_ENDPOINT: _FakeResp(json_value=INST_ROWS_1014)})
    hist = taifex.fetch_daily(path)
    assert len(hist) == 1
    saved = pd.read_csv(path, dtype={"date": str})
    assert list(saved.columns) == taifex.HISTORY_COLUMNS
    row = saved.iloc[0]
    assert row["date"] == "2026-10-14"
    assert [row[c] for c in ("open", "high", "low", "close", "volume")] == [
        48789.0, 49200.0, 48650.0, 48792.0, 44607.0]
    assert [row[c] for c in ("foreign_net", "trust_net", "dealer_net")] == [12345.0, -678.0, 90.0]


def test_inst_non_json_raises_and_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "hist.csv"
    prior = {"date": "2026-10-13", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
             "volume": 10.0, "foreign_net": 1.0, "trust_net": 2.0, "dealer_net": 3.0}
    taifex.append_row(taifex.load_history(str(path)), prior).to_csv(path, index=False)
    before = path.read_bytes()
    _patch_get(monkeypatch, {taifex.OHLC_ENDPOINT: _csv_resp(),
                             taifex.INST_ENDPOINT: _csv_resp(b"a,b\n1,2\n")})
    with pytest.raises(RuntimeError, match="非 JSON"):
        taifex.fetch_daily(str(path))
    assert path.read_bytes() == before


def test_csv_missing_column_raises(monkeypatch):
    _patch_get(monkeypatch, {taifex.OHLC_ENDPOINT: _csv_resp(_csv_without_session_column())})
    with pytest.raises(ValueError, match="交易時段"):
        taifex._get_rows(taifex.OHLC_ENDPOINT, taifex._OHLC_CSV_MAP)


def test_csv_missing_column_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "hist.csv"
    _patch_get(monkeypatch, {taifex.OHLC_ENDPOINT: _csv_resp(_csv_without_session_column()),
                             taifex.INST_ENDPOINT: _FakeResp(json_value=INST_ROWS_1014)})
    with pytest.raises(ValueError, match="交易時段"):
        taifex.fetch_daily(str(path))
    assert not path.exists()


def test_json_path_unchanged(monkeypatch):
    payload = [{"Date": "20261014"}]
    _patch_get(monkeypatch, {"u": _FakeResp(json_value=payload)})
    assert taifex._get_rows("u", taifex._OHLC_CSV_MAP) is payload
    _patch_get(monkeypatch, {"u": _FakeResp(json_value=payload)})
    assert taifex._get_rows("u", None) is payload


def test_real_response_with_bom_csv_is_not_json(monkeypatch):
    # 用真的 requests.Response 釘住「BOM CSV 會讓 json() 拋 ValueError」這個前提
    resp = requests.Response()
    resp.status_code = 200
    resp._content = _csv_bytes()
    _patch_get(monkeypatch, {taifex.OHLC_ENDPOINT: resp})
    rows = taifex._get_rows(taifex.OHLC_ENDPOINT, taifex._OHLC_CSV_MAP)
    assert taifex.parse_ohlc(rows) == EXPECTED_1014
