# core/planner.py — F-05 交易計畫產生器
# 停損:結構極值(近 10 根,含當根)與 1.5×ATR14 取「較近者」(離進場較近)
# 目標:2R 與前波高/低(前 20 根,不含當根)取「較近者」
# R:R = (目標-進場)/(進場-停損);< 1.5 → rejected(AC-10)
# 進場參考價 = 訊號K收盤(實際成交由 broker 以次K開盤±滑價決定,ADR-002)
from __future__ import annotations

import pandas as pd

import config
from core.models import Plan, Signal
from signals.technical import atr


def make_plan(signal: Signal, df: pd.DataFrame) -> Plan:
    """df:訊號當根為最後一列的 OHLC 歷史(chip 軌用台指期日K)。"""
    if signal.suppressed or signal.direction not in ("long", "short"):
        return Plan(signal=signal, status="rejected", reject_reason="not_actionable")

    close = float(df["close"].iloc[-1])
    d = 1 if signal.direction == "long" else -1

    atr14 = atr(df, config.ATR_WINDOW)
    a = float(atr14.iloc[-1]) if pd.notna(atr14.iloc[-1]) else 0.0
    if a <= 0:
        return Plan(signal=signal, status="rejected", reject_reason="atr_unavailable")

    # 停損候選:結構極值 vs 1.5×ATR;取離進場「較近者」
    struct_win = df.iloc[-config.STRUCTURE_WINDOW:]
    if signal.direction == "long":
        structural = float(struct_win["low"].astype(float).min())
        atr_stop = close - config.STOP_ATR_MULT * a
        stop = max(structural, atr_stop)
    else:
        structural = float(struct_win["high"].astype(float).max())
        atr_stop = close + config.STOP_ATR_MULT * a
        stop = min(structural, atr_stop)

    risk = (close - stop) * d
    if risk <= 0:
        return Plan(signal=signal, status="rejected", reject_reason="invalid_stop")

    # 目標候選:2R vs 前波極值;取離進場「較近者」
    prior = df.iloc[-(config.PRIOR_EXTREME_WINDOW + 1):-1]
    two_r = close + d * config.TARGET_R * risk
    if len(prior) and signal.direction == "long":
        prior_extreme = float(prior["high"].astype(float).max())
        target = min(two_r, prior_extreme) if prior_extreme > close else two_r
    elif len(prior):
        prior_extreme = float(prior["low"].astype(float).min())
        target = max(two_r, prior_extreme) if prior_extreme < close else two_r
    else:
        target = two_r

    reward = (target - close) * d
    if reward <= 0:
        return Plan(signal=signal, status="rejected", reject_reason="invalid_target")

    rr = reward / risk
    slip = config.slippage(signal.track, signal.symbol)
    entry_zone = (min(close - slip, close + slip), max(close - slip, close + slip))
    invalidation = "次K開盤已越過停損則不進場;進場後 %d 根K未觸發停損/目標以收盤價出場" % config.MAX_HOLD_BARS

    plan = Plan(
        signal=signal, entry_ref=close, entry_zone=entry_zone,
        stop=stop, target=target, invalidation=invalidation,
        rr=round(rr, 4),
    )
    if rr < config.MIN_RR:
        plan.status = "rejected"
        plan.reject_reason = "rr_below_min"
    return plan
