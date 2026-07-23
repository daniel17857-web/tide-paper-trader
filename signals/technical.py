# signals/technical.py — F-04 技術軌三訊號(外匯,週期無關:日K/小時K 同規則)
# 1. breakout:收盤 > 前 20 根最高(long)/ < 前 20 根最低(short),不含當根
# 2. pullback:趨勢中(EMA20 較 5 根前上升/下降)拉回觸及 EMA20±0.1% 後收復
#    (long:low ≤ EMA*(1+tol) 且 close > EMA;short 鏡像)
# 3. momentum:單根實體 |close-open| ≥ 1.5×ATR14(TR 的 14 根簡單平均)且順勢
# 同根多訊號:突破 > 動能 > 回調,只留一個,其餘 suppressed=True(AC-08)。
from __future__ import annotations

import pandas as pd

import config
from core.models import Signal


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def atr(df: pd.DataFrame, window: int) -> pd.Series:
    """TR = max(H-L, |H-前收|, |L-前收|) 的 window 根簡單移動平均。"""
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def _detect_last_bar(df: pd.DataFrame, symbol: str, timeframe: str) -> list[Signal]:
    n = len(df)
    if n < config.BREAKOUT_WINDOW + 1:
        return []
    ts = str(df["timestamp"].iloc[-1])
    o = float(df["open"].iloc[-1])
    h = float(df["high"].iloc[-1])
    lo = float(df["low"].iloc[-1])
    c = float(df["close"].iloc[-1])

    signals: list[Signal] = []

    def sig(kind: str, direction: str, **meta) -> Signal:
        return Signal(track="tech", symbol=symbol, date=ts, kind=kind,
                      direction=direction, timeframe=timeframe, meta=meta)

    # breakout(前 20 根,不含當根)
    prior = df.iloc[-(config.BREAKOUT_WINDOW + 1):-1]
    hh = float(prior["high"].astype(float).max())
    ll = float(prior["low"].astype(float).min())
    if c > hh:
        signals.append(sig("breakout", "long", level=hh))
    elif c < ll:
        signals.append(sig("breakout", "short", level=ll))

    # 趨勢方向:EMA20 vs 5 根前
    ema20 = ema(df["close"].astype(float), config.EMA_WINDOW)
    e_now = float(ema20.iloc[-1])
    e_prev = float(ema20.iloc[-1 - config.EMA_TREND_LOOKBACK])
    trend = "up" if e_now > e_prev else ("down" if e_now < e_prev else "flat")

    # momentum:實體 ≥ 1.5×ATR14 且順勢
    atr14 = atr(df, config.ATR_WINDOW)
    a = float(atr14.iloc[-1]) if pd.notna(atr14.iloc[-1]) else 0.0
    body = abs(c - o)
    if a > 0 and body >= config.MOMENTUM_ATR_MULT * a:
        if c > o and trend == "up":
            signals.append(sig("momentum", "long", body=round(body, 6), atr=round(a, 6)))
        elif c < o and trend == "down":
            signals.append(sig("momentum", "short", body=round(body, 6), atr=round(a, 6)))

    # pullback:趨勢中觸及 EMA 附近後收復
    tol = config.PULLBACK_TOLERANCE
    if trend == "up" and lo <= e_now * (1 + tol) and c > e_now:
        signals.append(sig("pullback", "long", ema=round(e_now, 6)))
    elif trend == "down" and h >= e_now * (1 - tol) and c < e_now:
        signals.append(sig("pullback", "short", ema=round(e_now, 6)))

    return _suppress(signals)


def _suppress(signals: list[Signal]) -> list[Signal]:
    if len(signals) <= 1:
        return signals
    rank = {k: i for i, k in enumerate(config.TECH_PRIORITY)}
    signals = sorted(signals, key=lambda s: rank.get(s.kind, 99))
    winner = signals[0]
    losers = [
        Signal(track=s.track, symbol=s.symbol, date=s.date, kind=s.kind,
               direction=s.direction, timeframe=s.timeframe,
               suppressed=True, meta={**s.meta, "suppressed_by": winner.kind})
        for s in signals[1:]
    ]
    return [winner] + losers


def detect(df: pd.DataFrame, symbol: str, timeframe: str = "1d") -> list[Signal]:
    """對最後一根K產訊號。df 欄位:timestamp, open, high, low, close。"""
    if len(df) == 0:
        return []
    return _detect_last_bar(df, symbol, timeframe)


def detect_range(df: pd.DataFrame, symbol: str, timeframe: str = "1d") -> list[Signal]:
    """逐根重播(fixture 比對用,AC-07)。"""
    out: list[Signal] = []
    for i in range(1, len(df) + 1):
        out.extend(detect(df.iloc[:i], symbol, timeframe))
    return out
