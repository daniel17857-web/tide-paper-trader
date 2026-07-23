# signals/chip.py — F-03 籌碼軌四訊號(台指期法人資料)
# 1. zscore:外資或投信淨部位 vs 前 20 日自身基線 |z| ≥ 2σ(σ 為母體標準差)
# 2. streak_buy:外資淨部位 > 0 連續 ≥ 5 日 → long
# 3. streak_sell:外資淨部位 < 0 連續 ≥ 5 日 → short
# 4. resonance:外資與投信淨部位「當日同號且雙雙較前日同向增減」→ 共振
# 同日多訊號:依 config.CHIP_PRIORITY(resonance > zscore > streak)只留一個
# 進場訊號,其餘 suppressed=True(與技術軌 AC-08 同規則,保持雙軌公平)。
# 資料不足 20 日:zscore 回傳 meta={"insufficient_data": True} 的標記訊號,
# 不產生偽訊號、不報錯(AC-06)。
from __future__ import annotations

import pandas as pd

import config
from core.models import Signal


def _zscore_signals(df: pd.DataFrame, date: str) -> list[Signal]:
    """外資/投信各自對前 20 日基線算 z-score(基線不含當日)。"""
    out: list[Signal] = []
    n = len(df)
    for col, name in (("foreign_net", "foreign"), ("trust_net", "trust")):
        series = df[col].astype(float)
        if n < config.ZSCORE_WINDOW + 1:
            out.append(Signal(
                track="chip", symbol=config.MTX_SYMBOL, date=date,
                kind="zscore", direction="none", suppressed=True,
                meta={"insufficient_data": True, "institution": name},
            ))
            continue
        baseline = series.iloc[-(config.ZSCORE_WINDOW + 1):-1]
        mean = float(baseline.mean())
        std = float(baseline.std(ddof=0))
        today = float(series.iloc[-1])
        if std == 0.0:
            continue
        z = (today - mean) / std
        if abs(z) >= config.ZSCORE_THRESHOLD:
            out.append(Signal(
                track="chip", symbol=config.MTX_SYMBOL, date=date,
                kind="zscore", direction="long" if z > 0 else "short",
                meta={"institution": name, "z": round(z, 4)},
            ))
    return out


def _streak_signals(df: pd.DataFrame, date: str) -> list[Signal]:
    net = df["foreign_net"].astype(float)
    if len(net) < config.STREAK_DAYS:
        return []
    tail = net.iloc[-config.STREAK_DAYS:]
    if (tail > 0).all():
        direction = "long"
    elif (tail < 0).all():
        direction = "short"
    else:
        return []
    return [Signal(
        track="chip", symbol=config.MTX_SYMBOL, date=date,
        kind="streak", direction=direction,
        meta={"days": int(config.STREAK_DAYS)},
    )]


def _resonance_signals(df: pd.DataFrame, date: str) -> list[Signal]:
    if len(df) < 2:
        return []
    f_today, f_prev = float(df["foreign_net"].iloc[-1]), float(df["foreign_net"].iloc[-2])
    t_today, t_prev = float(df["trust_net"].iloc[-1]), float(df["trust_net"].iloc[-2])
    if f_today > 0 and t_today > 0 and f_today > f_prev and t_today > t_prev:
        direction = "long"
    elif f_today < 0 and t_today < 0 and f_today < f_prev and t_today < t_prev:
        direction = "short"
    else:
        return []
    return [Signal(
        track="chip", symbol=config.MTX_SYMBOL, date=date,
        kind="resonance", direction=direction,
        meta={"foreign_net": f_today, "trust_net": t_today},
    )]


def _suppress(signals: list[Signal]) -> list[Signal]:
    """依優先序只留一個進場訊號;其餘複製為 suppressed=True。"""
    live = [s for s in signals if not s.suppressed and s.direction in ("long", "short")]
    markers = [s for s in signals if s.suppressed or s.direction == "none"]
    if len(live) <= 1:
        return live + markers
    rank = {k: i for i, k in enumerate(config.CHIP_PRIORITY)}
    live.sort(key=lambda s: rank.get(s.kind, 99))
    winner = live[0]
    losers = [
        Signal(track=s.track, symbol=s.symbol, date=s.date, kind=s.kind,
               direction=s.direction, timeframe=s.timeframe,
               suppressed=True, meta={**s.meta, "suppressed_by": winner.kind})
        for s in live[1:]
    ]
    return [winner] + losers + markers


def detect(df: pd.DataFrame) -> list[Signal]:
    """對歷史檔最後一日產訊號。df 欄位:date, ..., foreign_net, trust_net, dealer_net。"""
    if len(df) == 0:
        return []
    date = str(df["date"].iloc[-1])
    signals = (
        _resonance_signals(df, date)
        + _zscore_signals(df, date)
        + _streak_signals(df, date)
    )
    return _suppress(signals)


def detect_range(df: pd.DataFrame) -> list[Signal]:
    """逐日重播全歷史(fixture 比對用,AC-05)。"""
    out: list[Signal] = []
    for i in range(1, len(df) + 1):
        out.extend(detect(df.iloc[:i]))
    return out
