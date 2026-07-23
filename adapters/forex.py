# adapters/forex.py — F-02:四貨幣對日K + 小時K(yfinance,A-01)
# 歷史檔:data/fx_{pair}_{1d|1h}.csv,timestamp 去重累積(AC-03)。
# 任一貨幣對失敗:其餘照常更新、記 log、整體以非 0 exit code 告警(AC-04)。
from __future__ import annotations

import logging
import os

import pandas as pd

import config

log = logging.getLogger(__name__)

BAR_COLUMNS = ["timestamp", "open", "high", "low", "close"]


def history_path(pair: str, timeframe: str) -> str:
    return os.path.join(config.DATA_DIR, f"fx_{pair}_{timeframe}.csv")


def load_history(pair: str, timeframe: str) -> pd.DataFrame:
    path = history_path(pair, timeframe)
    if os.path.exists(path):
        return pd.read_csv(path, dtype={"timestamp": str})
    return pd.DataFrame(columns=BAR_COLUMNS)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance 回傳 → 標準欄位,timestamp 為 ISO 字串。"""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=BAR_COLUMNS)
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] for c in df.columns]
    out = pd.DataFrame({
        "timestamp": [ts.isoformat() for ts in df.index],
        "open": df["Open"].to_numpy(dtype=float),
        "high": df["High"].to_numpy(dtype=float),
        "low": df["Low"].to_numpy(dtype=float),
        "close": df["Close"].to_numpy(dtype=float),
    })
    return out.dropna()


def merge_history(history: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """去重合併:既有 timestamp 不覆寫(歷史不可變),只補新棒。"""
    if len(history) == 0:
        return fresh.reset_index(drop=True)
    seen = set(history["timestamp"].astype(str))
    add = fresh[~fresh["timestamp"].astype(str).isin(seen)]
    if len(add) == 0:
        return history
    return pd.concat([history, add], ignore_index=True)


def _download(pair: str, timeframe: str) -> pd.DataFrame:
    import yfinance as yf
    ticker = config.FX_YF_TICKERS[pair]
    if timeframe == "1d":
        raw = yf.download(ticker, period="3mo", interval="1d",
                          progress=False, auto_adjust=True)
    else:
        raw = yf.download(ticker, period="1mo", interval="1h",
                          progress=False, auto_adjust=True)
    return _normalize(raw)


def fetch(timeframes: list[str]) -> tuple[dict, list]:
    """抓所有貨幣對指定週期。回傳 (histories, failures)。
    histories: {(pair, timeframe): DataFrame};failures: [(pair, timeframe, err)]。
    """
    histories: dict = {}
    failures: list = []
    for pair in config.FX_PAIRS:
        for tf in timeframes:
            try:
                fresh = _download(pair, tf)
                if len(fresh) == 0:
                    raise RuntimeError("空回應")
                merged = merge_history(load_history(pair, tf), fresh)
                path = history_path(pair, tf)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                merged.to_csv(path, index=False)
                histories[(pair, tf)] = merged
                log.info("FX %s %s 更新至 %s(共 %d 棒)",
                         pair, tf, merged["timestamp"].iloc[-1], len(merged))
            except Exception as exc:  # noqa: BLE001 — 單一失敗不可拖垮其餘(AC-04)
                failures.append((pair, tf, str(exc)))
                log.error("FX %s %s 抓取失敗:%s", pair, tf, exc)
    return histories, failures


def fetch_daily() -> tuple[dict, list]:
    return fetch(["1d"])


def fetch_hourly() -> tuple[dict, list]:
    return fetch(["1h"])
