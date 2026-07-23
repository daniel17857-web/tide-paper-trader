# core/risk.py — F-06 風控模組(T-07,核心錯不得)
# 檢查順序:回撤熔斷 → 曝險上限 → 部位計算(AC-11/12/13)
# 部位公式(模擬允許小數口數,使單筆風險精確 = 虛擬資金 × 1%):
#   chip(小台):size 口 = 資金×1% / (50 × 停損距離點數)
#   tech USD 計價對(EURUSD/GBPUSD/AUDUSD):size 名目單位 = 資金×1% / 停損距離
#   tech USDJPY:size = 資金×1% × 停損價 / 停損距離
#     (損益以出場時匯率換回 USD:pnl = size×Δ價/出場價,停損時恰為 1% 風險)
from __future__ import annotations

import config
from core.models import Order, Plan, PortfolioState


def position_size(plan: Plan) -> float:
    track = plan.signal.track
    capital = config.CAPITAL[track]
    risk_amount = capital * config.RISK_PCT
    stop_dist = abs(plan.entry_ref - plan.stop)
    if stop_dist <= 0:
        return 0.0
    if track == "chip":
        return risk_amount / (config.MTX_POINT_VALUE * stop_dist)
    if "JPY" in plan.signal.symbol:
        return risk_amount * plan.stop / stop_dist
    return risk_amount / stop_dist


def check(plan: Plan, state: PortfolioState) -> Order:
    """Approved(含 size)或 Rejected(reason)。既有持倉一律不受影響(AC-13)。"""
    if plan.status != "ok":
        return Order(plan=plan, status="rejected", reject_reason="plan_" + plan.reject_reason)

    track = plan.signal.track
    if state.drawdown(track) >= config.DRAWDOWN_HALT:
        return Order(plan=plan, status="rejected", reject_reason="drawdown_halt")

    if state.open_count() >= config.MAX_OPEN_TOTAL:
        return Order(plan=plan, status="rejected", reject_reason="exposure_cap")
    if state.open_count(track) >= config.MAX_OPEN_PER_TRACK:
        return Order(plan=plan, status="rejected", reject_reason="exposure_cap_track")
    if any(p.symbol == plan.signal.symbol for p in state.positions):
        return Order(plan=plan, status="rejected", reject_reason="duplicate_symbol")

    size = position_size(plan)
    if size <= 0:
        return Order(plan=plan, status="rejected", reject_reason="zero_size")
    return Order(plan=plan, size=size, status="approved")
