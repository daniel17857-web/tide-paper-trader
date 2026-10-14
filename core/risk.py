# core/risk.py — F-06 風控模組(R2-01/R2-05 改版)
# 檢查順序:軌道終止 → 回撤熔斷 → 曝險上限 → 重複標的 → 預估部位 > 0
# R2-01:風控核發「資格單」——size 於此僅為 entry_ref 基準的預估值,
#         實際 size 由 broker 於成交時以實際進場價重算(position_size_at_fill)。
# 部位公式(模擬允許小數口數,使單筆風險精確 = 虛擬資金 × RISK_PCT):
#   chip(小台):size 口 = 資金×1% / (50 × |進場−停損|)
#   tech USD 計價對:size 名目單位 = 資金×1% / |進場−停損|
#   tech USDJPY:size = 資金×1% × 停損價 / |進場−停損|
#     (分子乘數維持 stop:停損出場經換匯後恰為資本 1%——勿改為進場價,
#      否則停損實虧 = 1%×(entry/stop) ≠ 1%,見 R2-01 稽核注記)
from __future__ import annotations

import config
from core.models import TRACK_HALTED, TRACK_TERMINATED, Order, Plan, PortfolioState


def _size(track: str, symbol: str, entry: float, stop: float) -> float:
    capital = config.CAPITAL[track]
    risk_amount = capital * config.RISK_PCT
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0
    if track == "chip":
        return risk_amount / (config.MTX_POINT_VALUE * stop_dist)
    if "JPY" in symbol:
        return risk_amount * stop / stop_dist
    return risk_amount / stop_dist


def position_size(plan: Plan) -> float:
    """預估部位(entry_ref 基準)。僅供風控預檢,非成交 size。"""
    return _size(plan.signal.track, plan.signal.symbol, plan.entry_ref, plan.stop)


def position_size_at_fill(track: str, symbol: str, actual_entry: float,
                          stop: float) -> float:
    """R2-01:成交時以實際進場價計定部位。輸入僅限成交瞬間已知量
    (成交K開盤推得的 actual_entry、事前已定的 stop),禁止引用該K 高/低/收。"""
    return _size(track, symbol, actual_entry, stop)


def risk_nominal(track: str) -> float:
    return config.CAPITAL[track] * config.RISK_PCT


def check(plan: Plan, state: PortfolioState) -> Order:
    """資格單(approved,size=預估)或 Rejected(reason)。既有持倉一律不受影響(AC-13)。"""
    if plan.status != "ok":
        return Order(plan=plan, status="rejected", reject_reason="plan_" + plan.reject_reason)

    track = plan.signal.track
    ts = state.status_of(track)
    if ts.status == TRACK_TERMINATED:
        return Order(plan=plan, status="rejected", reject_reason="track_terminated")
    if ts.status == TRACK_HALTED or state.drawdown(track) >= config.DRAWDOWN_HALT:
        return Order(plan=plan, status="rejected", reject_reason="drawdown_halt")

    if state.open_count() >= config.MAX_OPEN_TOTAL:
        return Order(plan=plan, status="rejected", reject_reason="exposure_cap")
    if state.open_count(track) >= config.MAX_OPEN_PER_TRACK:
        return Order(plan=plan, status="rejected", reject_reason="exposure_cap_track")
    if any(p.symbol == plan.signal.symbol for p in state.positions):
        return Order(plan=plan, status="rejected", reject_reason="duplicate_symbol")

    est = position_size(plan)
    if est <= 0:
        return Order(plan=plan, status="rejected", reject_reason="zero_size")
    return Order(plan=plan, size=est, status="approved")
