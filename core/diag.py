# core/diag.py — R2-08 訊號端遙測:signal_diag.csv(append-only)
# 以既有 df 平行唯讀重算指標與門檻距離;不動 signals/ 與任何交易規則。
# 寫入失敗不得中斷主管線(呼叫端以 try/except 包覆)。
# 固定輸出:每次執行,chip 當日一列 resonance 診斷 + 每法人 zscore 診斷;
# tech 每 (symbol, timeframe) 當根一列 momentum/breakout 診斷;
# 另對每個實際產出的訊號一列(含被抑制/planner 拒/風控拒原因)。
# 嚴禁從本檔數據期中反推調參(prereg 條款;診斷只餵第三輪設計)。
from __future__ import annotations

import csv
import os

import pandas as pd

import config
from signals.technical import atr, ema

SIGNAL_DIAG_PATH = os.path.join(config.R2_STATE_DIR, "signal_diag.csv")
DIAG_COLUMNS = ["date", "track", "symbol", "timeframe", "kind", "status", "detail"]


def _fmt(pairs: list[tuple]) -> str:
    return ";".join(f"{k}={v}" for k, v in pairs)


def chip_rows(df: pd.DataFrame) -> list[dict]:
    """台指期當日:resonance 四子條件與 zscore 門檻距離(無論是否觸發)。"""
    if len(df) < 2:
        return []
    date = str(df["date"].iloc[-1])
    rows = []
    f_t, f_p = float(df["foreign_net"].iloc[-1]), float(df["foreign_net"].iloc[-2])
    t_t, t_p = float(df["trust_net"].iloc[-1]), float(df["trust_net"].iloc[-2])
    rows.append({
        "date": date, "track": "chip", "symbol": config.MTX_SYMBOL,
        "timeframe": "1d", "kind": "resonance", "status": "metric",
        "detail": _fmt([
            ("foreign_net", f_t), ("trust_net", t_t),
            ("same_sign", int((f_t > 0 and t_t > 0) or (f_t < 0 and t_t < 0))),
            ("foreign_delta", round(f_t - f_p, 1)),
            ("trust_delta", round(t_t - t_p, 1)),
        ]),
    })
    n = len(df)
    for col, name in (("foreign_net", "foreign"), ("trust_net", "trust")):
        if n < config.ZSCORE_WINDOW + 1:
            z_txt = "insufficient"
        else:
            base = df[col].astype(float).iloc[-(config.ZSCORE_WINDOW + 1):-1]
            std = float(base.std(ddof=0))
            z_txt = round((float(df[col].iloc[-1]) - float(base.mean())) / std, 3) \
                if std > 0 else "zero_std"
        rows.append({
            "date": date, "track": "chip", "symbol": config.MTX_SYMBOL,
            "timeframe": "1d", "kind": "zscore", "status": "metric",
            "detail": _fmt([("institution", name), ("z", z_txt),
                            ("threshold", config.ZSCORE_THRESHOLD)]),
        })
    return rows


def tech_rows(df: pd.DataFrame, symbol: str, timeframe: str) -> list[dict]:
    """外匯當根:momentum 實體/ATR 比、breakout 距離、EMA 趨勢(無論是否觸發)。"""
    if len(df) < config.BREAKOUT_WINDOW + 1:
        return []
    ts = str(df["timestamp"].iloc[-1])
    o, c = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])
    a_series = atr(df, config.ATR_WINDOW)
    a = float(a_series.iloc[-1]) if pd.notna(a_series.iloc[-1]) else 0.0
    body_ratio = round(abs(c - o) / a, 3) if a > 0 else "atr_zero"
    e = ema(df["close"].astype(float), config.EMA_WINDOW)
    e_now = float(e.iloc[-1])
    e_prev = float(e.iloc[-1 - config.EMA_TREND_LOOKBACK])
    trend = "up" if e_now > e_prev else ("down" if e_now < e_prev else "flat")
    prior = df.iloc[-(config.BREAKOUT_WINDOW + 1):-1]
    hh = float(prior["high"].astype(float).max())
    ll = float(prior["low"].astype(float).min())
    return [{
        "date": ts, "track": "tech", "symbol": symbol, "timeframe": timeframe,
        "kind": "momentum", "status": "metric",
        "detail": _fmt([("body_atr_ratio", body_ratio),
                        ("threshold", config.MOMENTUM_ATR_MULT),
                        ("trend", trend), ("bull", int(c > o)),
                        ("close_vs_hh", round(c - hh, 6)),
                        ("close_vs_ll", round(c - ll, 6))]),
    }]


def signal_rows(signals: list, outcomes: dict) -> list[dict]:
    """每個實際產出的訊號一列。outcomes: {(symbol, timeframe, kind): 原因碼或 'approved'}。"""
    rows = []
    for s in signals:
        if s.suppressed:
            status = "suppressed_by:" + str(s.meta.get("suppressed_by", ""))
        elif s.direction == "none":
            status = "insufficient_data"
        else:
            status = outcomes.get((s.symbol, s.timeframe, s.kind), "no_outcome")
        rows.append({
            "date": s.date, "track": s.track, "symbol": s.symbol,
            "timeframe": s.timeframe, "kind": s.kind, "status": status,
            "detail": _fmt(sorted((k, v) for k, v in s.meta.items()
                                  if k != "suppressed_by")),
        })
    return rows


def append(rows: list[dict], path: str = SIGNAL_DIAG_PATH) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=DIAG_COLUMNS)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)
