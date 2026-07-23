# core/broker.py — F-07 模擬撮合引擎 + 台帳(T-08,核心錯不得)
# 規則(ADR-002,全程零隨機):
#   進場:訊號次一根K開盤價 ± 滑價(long 加、short 減皆為不利方向)
#   跳空保護:次K開盤已越過停損或目標 → 不進場(invalidated)
#   出場判定(每根新K,含進場當根):
#     1) 觸及停損(long: low≤stop / short: high≥stop)→ 以停損價出場
#     2) 觸及目標(long: high≥target / short: low≤target)→ 以目標價出場
#     同K雙觸發 → 以停損計(保守偏差,交接紅線 5,不得優化)
#     3) 持倉滿 MAX_HOLD_BARS 根未觸發 → 以收盤價出場(invalidated)
# 台帳 state/ledger.csv 為 append-only:只允許附加,禁止改寫歷史(紅線 4)。
from __future__ import annotations

import csv
import os

import pandas as pd

import config
from core.models import (
    LEDGER_COLUMNS, Order, PortfolioState, Position, Trade,
)

POSITIONS_PATH = os.path.join(config.STATE_DIR, "positions.csv")
EQUITY_PATH = os.path.join(config.STATE_DIR, "equity.csv")
LEDGER_PATH = os.path.join(config.STATE_DIR, "ledger.csv")

POSITION_COLUMNS = [
    "track", "symbol", "timeframe", "kind", "direction", "size",
    "entry_date", "entry_price", "stop", "target", "bars_held",
]


# ── 損益換算(軌別本位幣)──

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
    risk = abs(entry - stop)
    if risk == 0:
        return 0.0
    return (exit_price - entry) * d / risk


# ── 撮合 ──

def _try_fill(order: Order, bar: dict) -> Position | None:
    """次K開盤 ± 滑價進場;開盤已越過停損/目標則不進場。"""
    plan = order.plan
    sig = plan.signal
    d = 1 if sig.direction == "long" else -1
    open_price = float(bar["open"])
    if (open_price - plan.stop) * d <= 0:      # 跳空穿停損
        return None
    if (plan.target - open_price) * d <= 0:    # 跳空穿目標(行情已走完)
        return None
    entry = open_price + d * config.slippage(sig.track, sig.symbol)
    return Position(
        track=sig.track, symbol=sig.symbol, timeframe=sig.timeframe,
        kind=sig.kind, direction=sig.direction, size=order.size,
        entry_date=str(bar["timestamp"]), entry_price=entry,
        stop=plan.stop, target=plan.target, bars_held=0,
    )


def _check_exit(pos: Position, bar: dict) -> tuple[float, str] | None:
    """回傳 (出場價, 原因) 或 None。停損優先於目標(同K雙觸發以停損計)。"""
    high, low = float(bar["high"]), float(bar["low"])
    if pos.direction == "long":
        if low <= pos.stop:
            return pos.stop, "stop"
        if high >= pos.target:
            return pos.target, "target"
    else:
        if high >= pos.stop:
            return pos.stop, "stop"
        if low <= pos.target:
            return pos.target, "target"
    if pos.bars_held >= config.MAX_HOLD_BARS:
        return float(bar["close"]), "invalidated"
    return None


def on_new_bar(state: PortfolioState, bars: dict,
               pending_orders: list[Order] | None = None) -> tuple[PortfolioState, list[Trade]]:
    """推進一根K。bars: {(symbol, timeframe): {timestamp, open, high, low, close}}。
    回傳 (新 state, 本K平倉交易)。state 就地更新後回傳(單執行緒批次管線)。
    """
    pending_orders = pending_orders or []
    closed: list[Trade] = []

    # 1) 新單進場(用該商品「本次執行的新K」= 訊號次K)
    for order in pending_orders:
        if order.status != "approved":
            continue
        key = (order.plan.signal.symbol, order.plan.signal.timeframe)
        bar = bars.get(key)
        if bar is None:
            continue
        pos = _try_fill(order, bar)
        if pos is not None:
            state.positions.append(pos)

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
        exit_price, reason = exit_info
        pnl = trade_pnl(pos.track, pos.symbol, pos.direction, pos.size,
                        pos.entry_price, exit_price)
        trade = Trade(
            track=pos.track, symbol=pos.symbol, timeframe=pos.timeframe,
            kind=pos.kind, direction=pos.direction, size=pos.size,
            entry_date=pos.entry_date, entry_price=pos.entry_price,
            exit_date=str(bar["timestamp"]), exit_price=exit_price,
            exit_reason=reason,
            r_multiple=round(r_multiple(pos.direction, pos.entry_price, pos.stop, exit_price), 4),
            pnl=round(pnl, 2),
        )
        closed.append(trade)
        state.equity[pos.track] = state.equity.get(pos.track, config.CAPITAL[pos.track]) + trade.pnl
        peak = state.peak_equity.get(pos.track, config.CAPITAL[pos.track])
        state.peak_equity[pos.track] = max(peak, state.equity[pos.track])
    state.positions = remaining
    return state, closed


# ── 狀態持久化(ADR-004:CSV in repo)──

def load_state(state_dir: str = config.STATE_DIR) -> PortfolioState:
    state = PortfolioState(
        equity=dict(config.CAPITAL),
        peak_equity=dict(config.CAPITAL),
    )
    pos_path = os.path.join(state_dir, "positions.csv")
    if os.path.exists(pos_path):
        df = pd.read_csv(pos_path, dtype={"symbol": str, "entry_date": str,
                                          "track": str, "timeframe": str})
        state.positions = [
            Position(track=str(r["track"]), symbol=str(r["symbol"]),
                     timeframe=str(r["timeframe"]), kind=str(r["kind"]),
                     direction=str(r["direction"]), size=float(r["size"]),
                     entry_date=str(r["entry_date"]), entry_price=float(r["entry_price"]),
                     stop=float(r["stop"]), target=float(r["target"]),
                     bars_held=int(r["bars_held"]))
            for _, r in df.iterrows()
        ]
    eq_path = os.path.join(state_dir, "equity.csv")
    if os.path.exists(eq_path):
        df = pd.read_csv(eq_path, dtype={"date": str, "track": str})
        for track in config.CAPITAL:
            rows = df[df["track"] == track]
            if len(rows):
                state.equity[track] = float(rows["equity"].iloc[-1])
                state.peak_equity[track] = float(rows["peak"].iloc[-1])
    return state


def save_state(state: PortfolioState, run_date: str,
               state_dir: str = config.STATE_DIR) -> None:
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


def append_trades(trades: list[Trade], path: str = LEDGER_PATH) -> None:
    """台帳唯一寫入口:append-only,永不重寫既有列(紅線 4)。"""
    if not trades:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        if new:
            w.writeheader()
        for t in trades:
            w.writerow(t.to_row())


def load_ledger(path: str = LEDGER_PATH) -> pd.DataFrame:
    if os.path.exists(path):
        return pd.read_csv(path, dtype={"symbol": str, "entry_date": str,
                                        "exit_date": str, "track": str})
    return pd.DataFrame(columns=LEDGER_COLUMNS)
