# core/broker.py — F-07 模擬撮合引擎 + 台帳(R2-01/02/03/04/05/06/07/08 改版)
# 規則(ADR-002 + R2,全程零隨機):
#   進場(R2-01/02):訊號次一根K開盤價 ± 滑價(不利方向)。成交前依序檢查:
#     gap_through_stop / gap_through_target(跳空保護,沿用第一輪)
#     stop_too_tight_at_fill(|實際進場−停損| < MIN_STOP_SLIP_RATIO×滑價)
#     rr_degraded_at_fill(以實際進場價複算 RR < MIN_RR)
#     通過後才以實際進場價計定 size(risk.position_size_at_fill)
#   出場(R2-03/04,每根新K,含進場當根):
#     1) 觸及停損 → 以「停損價 ∓ 滑價(不利)」出場
#     2) 觸及目標 → 以目標價出場(限價性質,不加有利滑價)
#     同K雙觸發 → 以停損計(保守偏差,紅線 5,不得優化)
#     3) 持倉滿 MAX_HOLD_BARS[timeframe] 根 → 以「收盤 ∓ 滑價」出場(invalidated)
# 台帳 state/r2/ledger.csv 為 append-only;第一輪三檔(state/*.csv)原地凍結(R2-06)。
from __future__ import annotations

import csv
import os

import pandas as pd

import config
from core import risk
from core.models import (
    LEDGER_COLUMNS, Order, PortfolioState, Position, Trade, TrackStatus,
)

POSITIONS_PATH = os.path.join(config.R2_STATE_DIR, "positions.csv")
EQUITY_PATH = os.path.join(config.R2_STATE_DIR, "equity.csv")
LEDGER_PATH = os.path.join(config.R2_STATE_DIR, "ledger.csv")
REJECTS_PATH = os.path.join(config.R2_STATE_DIR, "rejects.csv")
HALTS_PATH = os.path.join(config.R2_STATE_DIR, "halts.csv")
TRACK_STATUS_PATH = os.path.join(config.R2_STATE_DIR, "track_status.csv")

POSITION_COLUMNS = [
    "track", "symbol", "timeframe", "kind", "direction", "size",
    "entry_date", "entry_price", "stop", "target", "bars_held",
    "planned_entry_ref", "planned_rr", "fill_rr",
    "risk_nominal", "risk_actual", "slippage_cost_entry",
]

# 統一 reason 碼表(R2-08,單一來源;rejects.csv 只允許這些值)
REASON_CODES = {
    # 計畫層(planner)
    "stop_too_tight", "rr_below_min", "invalid_stop", "invalid_target",
    "atr_unavailable", "not_actionable",
    # 風控層(risk)
    "drawdown_halt", "exposure_cap", "exposure_cap_track",
    "duplicate_symbol", "zero_size", "track_terminated",
    # 成交層(broker)
    "gap_through_stop", "gap_through_target",
    "stop_too_tight_at_fill", "rr_degraded_at_fill",
}


# ── 損益與量測換算(軌別本位幣)──

def trade_pnl(track: str, symbol: str, direction: str, size: float,
              entry: float, exit_price: float) -> float:
    d = 1 if direction == "long" else -1
    move = (exit_price - entry) * d
    if track == "chip":
        return size * config.MTX_POINT_VALUE * move
    if "JPY" in symbol:
        return size * move / exit_price
    return size * move


def r_multiple(direction: str, entry: float, stop: float, exit_price: float) -> float:
    d = 1 if direction == "long" else -1
    risk_dist = abs(entry - stop)
    if risk_dist == 0:
        return 0.0
    return (exit_price - entry) * d / risk_dist


def actual_risk(track: str, symbol: str, size: float, entry: float,
                stop: float) -> float:
    """實際單筆風險(帳戶幣別)。USDJPY 以停損價換匯(與 sizing 公式互證)。"""
    dist = abs(entry - stop)
    if track == "chip":
        return size * config.MTX_POINT_VALUE * dist
    if "JPY" in symbol:
        return size * dist / stop
    return size * dist


def _price_to_ccy(track: str, symbol: str, size: float, price_diff: float,
                  rate: float) -> float:
    """把價差(絕對值)換成帳戶幣別金額;JPY 對以 rate 換匯。"""
    if track == "chip":
        return size * config.MTX_POINT_VALUE * price_diff
    if "JPY" in symbol:
        return size * price_diff / rate
    return size * price_diff


# ── 撮合 ──

def _try_fill(order: Order, bar: dict) -> tuple[Position | None, str]:
    """次K開盤 ± 滑價進場。回傳 (Position, "") 或 (None, reason)。
    R2-01:size 於此以實際進場價計定;僅使用成交K的 open(成交瞬間已知),
    禁止引用該K高/低/收(無前視)。"""
    plan = order.plan
    sig = plan.signal
    d = 1 if sig.direction == "long" else -1
    open_price = float(bar["open"])
    slip = config.slippage(sig.track, sig.symbol)

    if (open_price - plan.stop) * d <= 0:       # 跳空穿停損
        return None, "gap_through_stop"
    if (plan.target - open_price) * d <= 0:     # 跳空穿目標(行情已走完)
        return None, "gap_through_target"

    entry = open_price + d * slip
    stop_dist = (entry - plan.stop) * d
    if stop_dist < config.MIN_STOP_SLIP_RATIO * slip:   # R2-02 成交複檢
        return None, "stop_too_tight_at_fill"

    # 通過上式必有 stop_dist ≥ 10×slip > 0;reward ≤ 0(滑價使進場越過目標)
    # 即 fill RR ≤ 0 < MIN_RR,統一歸入 rr_degraded_at_fill(R2-01 成交 RR 複檢)
    reward = (plan.target - entry) * d
    fill_rr = reward / stop_dist
    if fill_rr < config.MIN_RR:
        return None, "rr_degraded_at_fill"

    size = risk.position_size_at_fill(sig.track, sig.symbol, entry, plan.stop)
    if size <= 0:
        return None, "zero_size"

    pos = Position(
        track=sig.track, symbol=sig.symbol, timeframe=sig.timeframe,
        kind=sig.kind, direction=sig.direction, size=size,
        entry_date=str(bar["timestamp"]), entry_price=entry,
        stop=plan.stop, target=plan.target, bars_held=0,
        planned_entry_ref=plan.entry_ref, planned_rr=plan.rr,
        fill_rr=round(fill_rr, 4),
        risk_nominal=risk.risk_nominal(sig.track),
        risk_actual=round(actual_risk(sig.track, sig.symbol, size, entry, plan.stop), 2),
        slippage_cost_entry=round(
            _price_to_ccy(sig.track, sig.symbol, size, slip, entry), 2),
    )
    return pos, ""


def _check_exit(pos: Position, bar: dict) -> tuple[float, str, float] | None:
    """回傳 (實際出場價, 原因, 出場滑價成本原始價差) 或 None。
    停損優先於目標(同K雙觸發以停損計);stop/invalidated 加不利滑價(R2-03)。"""
    high, low = float(bar["high"]), float(bar["low"])
    slip = config.slippage(pos.track, pos.symbol)
    d = pos.dir_mult
    max_hold = config.MAX_HOLD_BARS[pos.timeframe]  # R2-04:缺鍵即 KeyError
    if pos.direction == "long":
        if low <= pos.stop:
            return pos.stop - slip, "stop", slip
        if high >= pos.target:
            return pos.target, "target", 0.0
    else:
        if high >= pos.stop:
            return pos.stop + slip, "stop", slip
        if low <= pos.target:
            return pos.target, "target", 0.0
    if pos.bars_held >= max_hold:
        return float(bar["close"]) - d * slip, "invalidated", slip
    return None


def on_new_bar(state: PortfolioState, bars: dict,
               pending_orders: list[Order] | None = None,
               ) -> tuple[PortfolioState, list[Trade], list[dict]]:
    """推進一根K。bars: {(symbol, timeframe): {timestamp, open, high, low, close}}。
    回傳 (state, 本K平倉交易, 成交層拒單紀錄)。"""
    pending_orders = pending_orders or []
    closed: list[Trade] = []
    fill_rejects: list[dict] = []

    # 1) 新單進場(訊號次K;試單一次,成敗皆消耗)
    for order in pending_orders:
        if order.status != "approved":
            continue
        sig = order.plan.signal
        bar = bars.get((sig.symbol, sig.timeframe))
        if bar is None:
            continue
        pos, reason = _try_fill(order, bar)
        if pos is not None:
            state.positions.append(pos)
        else:
            fill_rejects.append({
                "date": str(bar["timestamp"]), "track": sig.track,
                "symbol": sig.symbol, "kind": sig.kind,
                "timeframe": sig.timeframe, "reason": reason,
            })

    # 2) 逐倉檢查出場(含進場當根;順序依持倉先後,零隨機)
    remaining: list[Position] = []
    for pos in state.positions:
        bar = bars.get((pos.symbol, pos.timeframe))
        if bar is None:
            remaining.append(pos)
            continue
        exit_info = _check_exit(pos, bar)
        if exit_info is None:
            pos.bars_held += 1
            remaining.append(pos)
            continue
        exit_price, reason, exit_slip = exit_info
        pnl = trade_pnl(pos.track, pos.symbol, pos.direction, pos.size,
                        pos.entry_price, exit_price)
        trade = Trade(
            track=pos.track, symbol=pos.symbol, timeframe=pos.timeframe,
            kind=pos.kind, direction=pos.direction, size=pos.size,
            entry_date=pos.entry_date, entry_price=pos.entry_price,
            exit_date=str(bar["timestamp"]), exit_price=exit_price,
            exit_reason=reason,
            r_multiple=round(r_multiple(pos.direction, pos.entry_price,
                                        pos.stop, exit_price), 4),
            pnl=round(pnl, 2),
            stop=pos.stop, target=pos.target,
            planned_entry_ref=pos.planned_entry_ref, planned_rr=pos.planned_rr,
            fill_rr=pos.fill_rr, risk_nominal=pos.risk_nominal,
            risk_actual=pos.risk_actual,
            slippage_cost_entry=pos.slippage_cost_entry,
            slippage_cost_exit=round(
                _price_to_ccy(pos.track, pos.symbol, pos.size, exit_slip,
                              exit_price), 2),
            round=config.ROUND,
        )
        closed.append(trade)
        state.equity[pos.track] = state.equity.get(pos.track, config.CAPITAL[pos.track]) + trade.pnl
        peak = state.peak_equity.get(pos.track, config.CAPITAL[pos.track])
        state.peak_equity[pos.track] = max(peak, state.equity[pos.track])
    state.positions = remaining
    return state, closed, fill_rejects


# ── 狀態持久化(ADR-004 + R2-06:第二輪一律在 state/r2/)──

def load_state(state_dir: str = config.R2_STATE_DIR) -> PortfolioState:
    state = PortfolioState(
        equity=dict(config.CAPITAL),
        peak_equity=dict(config.CAPITAL),
    )
    pos_path = os.path.join(state_dir, "positions.csv")
    if os.path.exists(pos_path):
        df = pd.read_csv(pos_path, dtype={"symbol": str, "entry_date": str,
                                          "track": str, "timeframe": str})
        for _, r in df.iterrows():
            kwargs = {c: r[c] for c in POSITION_COLUMNS if c in df.columns}
            for k in ("size", "entry_price", "stop", "target", "planned_entry_ref",
                      "planned_rr", "fill_rr", "risk_nominal", "risk_actual",
                      "slippage_cost_entry"):
                if k in kwargs:
                    kwargs[k] = float(kwargs[k])
            for k in ("track", "symbol", "timeframe", "kind", "direction",
                      "entry_date"):
                kwargs[k] = str(kwargs[k])
            kwargs["bars_held"] = int(r["bars_held"])
            state.positions.append(Position(**kwargs))
    eq_path = os.path.join(state_dir, "equity.csv")
    if os.path.exists(eq_path):
        df = pd.read_csv(eq_path, dtype={"date": str, "track": str})
        for track in config.CAPITAL:
            rows = df[df["track"] == track]
            if len(rows):
                state.equity[track] = float(rows["equity"].iloc[-1])
                state.peak_equity[track] = float(rows["peak"].iloc[-1])
    ts_path = os.path.join(state_dir, "track_status.csv")
    if os.path.exists(ts_path):
        df = pd.read_csv(ts_path, dtype=str)
        for _, r in df.iterrows():
            halt_date = "" if pd.isna(r["halt_date"]) else str(r["halt_date"])
            state.track_status[str(r["track"])] = TrackStatus(
                status=str(r["status"]), halt_date=halt_date,
                resets=int(r["resets"]))
    return state


def save_state(state: PortfolioState, run_date: str,
               state_dir: str = config.R2_STATE_DIR) -> None:
    os.makedirs(state_dir, exist_ok=True)
    pos_rows = [{c: getattr(p, c) for c in POSITION_COLUMNS} for p in state.positions]
    pd.DataFrame(pos_rows, columns=POSITION_COLUMNS).to_csv(
        os.path.join(state_dir, "positions.csv"), index=False)
    eq_path = os.path.join(state_dir, "equity.csv")
    new = not os.path.exists(eq_path)
    with open(eq_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "track", "equity", "peak"])
        for track in sorted(config.CAPITAL):
            w.writerow([run_date, track,
                        round(state.equity[track], 2),
                        round(state.peak_equity[track], 2)])
    with open(os.path.join(state_dir, "track_status.csv"), "w",
              newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["track", "status", "halt_date", "resets"])
        for track in sorted(config.CAPITAL):
            ts = state.status_of(track)
            w.writerow([track, ts.status, ts.halt_date, ts.resets])


def _append_rows(path: str, header: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def append_trades(trades: list[Trade], path: str = LEDGER_PATH) -> None:
    """台帳唯一寫入口:append-only,永不重寫既有列(紅線 4)。"""
    _append_rows(path, LEDGER_COLUMNS, [t.to_row() for t in trades])


REJECT_COLUMNS = ["date", "track", "symbol", "kind", "timeframe", "reason"]


def append_rejects(rows: list[dict], path: str = REJECTS_PATH) -> None:
    """R2-08 核駁落地(append-only)。reason 必須屬統一碼表。"""
    for r in rows:
        if r.get("reason") not in REASON_CODES:
            raise ValueError(f"未知 reason 碼:{r.get('reason')}")
    _append_rows(path, REJECT_COLUMNS, rows)


HALT_COLUMNS = ["date", "track", "equity", "peak", "reset_no"]


def append_halt(row: dict, path: str = HALTS_PATH) -> None:
    """R2-05 熔斷事件落地(append-only,每次熔斷恰一列)。"""
    _append_rows(path, HALT_COLUMNS, [row])


def load_ledger(path: str = LEDGER_PATH) -> pd.DataFrame:
    if os.path.exists(path):
        return pd.read_csv(path, dtype={"symbol": str, "entry_date": str,
                                        "exit_date": str, "track": str})
    return pd.DataFrame(columns=LEDGER_COLUMNS)
