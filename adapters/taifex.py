# adapters/taifex.py — F-01:台指期日K(OHLCV)+ 三大法人期貨淨部位
# 資料源:TAIFEX Open API(煙霧測試 2026-07-23 通過)
#   - 日K:  /v1/DailyMarketReportFut(取 TX 一般時段、最近月契約)
#   - 法人:/v1/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate
#           (ContractCode=臺股期貨,Item=外資/投信/自營商,取 OpenInterest(Net))
# 歷史檔:data/taifex_history.csv,append 累積、date 去重(AC-01/AC-02)。
from __future__ import annotations

import logging
import os

import pandas as pd
import requests

import config

log = logging.getLogger(__name__)

BASE = "https://openapi.taifex.com.tw/v1"
OHLC_ENDPOINT = f"{BASE}/DailyMarketReportFut"
INST_ENDPOINT = f"{BASE}/MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate"

HISTORY_COLUMNS = [
    "date", "open", "high", "low", "close", "volume",
    "foreign_net", "trust_net", "dealer_net",
]
HISTORY_PATH = os.path.join(config.DATA_DIR, "taifex_history.csv")

# 實際 API 的外資 Item 為「外資及陸資」(2026-07-23 實測),兩種寫法都接受
_INST_MAP = {"外資": "foreign_net", "外資及陸資": "foreign_net",
             "投信": "trust_net", "自營商": "dealer_net"}


def _get_json(url: str, timeout: int = 30) -> list:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "tide-paper-trader"})
    resp.raise_for_status()
    return resp.json()


def _iso_date(yyyymmdd: str) -> str:
    s = str(yyyymmdd).strip()
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def parse_ohlc(rows: list) -> dict | None:
    """從 DailyMarketReportFut 回應取 TX 一般時段最近月的 OHLCV。無資料回 None。"""
    tx = [
        r for r in rows
        if r.get("Contract") == "TX"
        and r.get("TradingSession") == "一般"
        and len(str(r.get("ContractMonth(Week)", ""))) == 6  # 排除週契約/價差
    ]
    if not tx:
        return None
    tx.sort(key=lambda r: str(r["ContractMonth(Week)"]))
    near = tx[0]
    try:
        return {
            "date": _iso_date(near["Date"]),
            "open": float(near["Open"]),
            "high": float(near["High"]),
            "low": float(near["Low"]),
            "close": float(near["Last"]),
            "volume": float(near["Volume"]),
        }
    except (ValueError, KeyError):
        log.warning("TAIFEX 日K欄位無法解析:%s", near)
        return None


def parse_institutional(rows: list) -> dict | None:
    """取臺股期貨三大法人淨未平倉口數。缺任一法人回 None。"""
    out: dict = {}
    for r in rows:
        if r.get("ContractCode") != "臺股期貨":
            continue
        col = _INST_MAP.get(str(r.get("Item", "")).strip())
        if col is None:
            continue
        try:
            out[col] = float(r["OpenInterest(Net)"])
            out.setdefault("date", _iso_date(r["Date"]))
        except (ValueError, KeyError):
            return None
    if set(_INST_MAP.values()) <= set(out):
        return out
    return None


def load_history(path: str = HISTORY_PATH) -> pd.DataFrame:
    if os.path.exists(path):
        return pd.read_csv(path, dtype={"date": str})
    return pd.DataFrame(columns=HISTORY_COLUMNS)


def append_row(history: pd.DataFrame, row: dict) -> pd.DataFrame:
    """去重 append:date 已存在則不動(AC-01 date 無重複)。"""
    if len(history) and row["date"] in set(history["date"].astype(str)):
        return history
    new = pd.DataFrame([{c: row.get(c) for c in HISTORY_COLUMNS}])
    if len(history) == 0:
        return new
    return pd.concat([history, new], ignore_index=True)


def fetch_daily(path: str = HISTORY_PATH) -> pd.DataFrame:
    """抓當日資料併入歷史檔。無新資料時不寫入空列、正常結束(AC-02)。"""
    ohlc = parse_ohlc(_get_json(OHLC_ENDPOINT))
    inst = parse_institutional(_get_json(INST_ENDPOINT))
    history = load_history(path)
    if ohlc is None or inst is None or ohlc["date"] != inst["date"]:
        log.info("無新資料(非交易日或尚未公布),不寫入。")
        return history
    row = {**ohlc, **{k: inst[k] for k in _INST_MAP.values()}}
    updated = append_row(history, row)
    if len(updated) > len(history):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        updated.to_csv(path, index=False)
        log.info("TAIFEX 新增 %s", row["date"])
    else:
        log.info("無新資料(%s 已存在)。", row["date"])
    return updated
