# scripts/run_pipeline.py — 全管線:抓取→撮合(補進場/出場)→訊號→計畫→風控→報告
# 模式:daily(台北16:30 主跑,台指期+外匯日K)/ london / newyork(小時K加掃)
# 跨執行狀態:
#   state/pending_orders.csv  上次執行核准、等次K進場的單
#   state/cursor.csv          各 (symbol,timeframe) 已處理到的K棒時間戳
# 執行順序(每根新K):先讓 broker 消化(進場+出場),最後才對最新K產生新訊號。
from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config
from adapters import forex, taifex
from core import broker, risk
from core.models import Order, Plan, Signal
from core.planner import make_plan
from report.build_report import write_report
from signals import chip, technical

log = logging.getLogger("pipeline")

PENDING_PATH = os.path.join(config.STATE_DIR, "pending_orders.csv")
CURSOR_PATH = os.path.join(config.STATE_DIR, "cursor.csv")
PENDING_COLUMNS = [
    "track", "symbol", "timeframe", "kind", "direction", "signal_date",
    "entry_ref", "stop", "target", "size",
]


def taipei_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


# ── pending orders 持久化 ──

def load_pending(path: str = PENDING_PATH) -> list[Order]:
    if not os.path.exists(path):
        return []
    df = pd.read_csv(path, dtype={"symbol": str, "signal_date": str})
    orders = []
    for _, r in df.iterrows():
        sig = Signal(track=str(r["track"]), symbol=str(r["symbol"]),
                     date=str(r["signal_date"]), kind=str(r["kind"]),
                     direction=str(r["direction"]), timeframe=str(r["timeframe"]))
        plan = Plan(signal=sig, entry_ref=float(r["entry_ref"]),
                    stop=float(r["stop"]), target=float(r["target"]))
        orders.append(Order(plan=plan, size=float(r["size"]), status="approved"))
    return orders


def save_pending(orders: list[Order], path: str = PENDING_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(PENDING_COLUMNS)
        for o in orders:
            s = o.plan.signal
            w.writerow([s.track, s.symbol, s.timeframe, s.kind, s.direction,
                        s.date, o.plan.entry_ref, o.plan.stop, o.plan.target, o.size])


# ── cursor 持久化 ──

def load_cursors(path: str = CURSOR_PATH) -> dict:
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype=str)
    return {(r["symbol"], r["timeframe"]): r["last_ts"] for _, r in df.iterrows()}


def save_cursors(cursors: dict, path: str = CURSOR_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "timeframe", "last_ts"])
        for (sym, tf), ts in sorted(cursors.items()):
            w.writerow([sym, tf, ts])


def _bars_df(key: tuple, df: pd.DataFrame) -> pd.DataFrame:
    """統一為 timestamp/open/high/low/close(台指期 date → timestamp)。"""
    if "timestamp" not in df.columns:
        df = df.rename(columns={"date": "timestamp"})
    return df[["timestamp", "open", "high", "low", "close"]].copy()


def advance_broker(series: dict, state, pending: list[Order],
                   cursors: dict) -> list:
    """把 cursor 之後的所有新K依時間序餵給 broker。回傳平倉交易。"""
    events: dict = {}
    for key, df in series.items():
        bars = _bars_df(key, df)
        cursor = cursors.get(key)
        if cursor is None:
            # 首次見到此序列:不重播歷史,cursor 直接設到最後一棒
            if len(bars):
                cursors[key] = str(bars["timestamp"].iloc[-1])
            continue
        new = bars[bars["timestamp"].astype(str) > str(cursor)]
        for _, r in new.iterrows():
            ts = str(r["timestamp"])
            events.setdefault(ts, {})[key] = {
                "timestamp": ts, "open": float(r["open"]), "high": float(r["high"]),
                "low": float(r["low"]), "close": float(r["close"]),
            }
            cursors[key] = max(cursors[key], ts)

    closed_all = []
    for ts in sorted(events):
        bars = events[ts]
        fillable, still_pending = [], []
        for o in pending:
            key = (o.plan.signal.symbol, o.plan.signal.timeframe)
            if key in bars and str(o.plan.signal.date) < ts:
                fillable.append(o)   # 訊號次一根K:試單一次,成敗皆消耗
            else:
                still_pending.append(o)
        state, closed = broker.on_new_bar(state, bars, fillable)
        pending[:] = still_pending
        closed_all.extend(closed)
    return closed_all


def generate_orders(signals: list[Signal], series: dict, state,
                    pending: list[Order] | None = None) -> tuple[list[Order], list]:
    """訊號 → 計畫 → 風控。回傳 (核准單, 全部計畫紀錄)。
    冪等保護:pending 已有同 (symbol, timeframe, 訊號日) 的單不重複下(重跑安全)。"""
    seen = {(o.plan.signal.symbol, o.plan.signal.timeframe, o.plan.signal.date)
            for o in (pending or [])}
    approved, records = [], []
    for s in signals:
        if s.suppressed or s.direction not in ("long", "short"):
            continue
        if (s.symbol, s.timeframe, s.date) in seen:
            log.info("略過(pending 已有同訊號):%s %s %s", s.symbol, s.timeframe, s.date)
            continue
        key = (s.symbol, s.timeframe)
        df = series.get(key)
        if df is None:
            continue
        plan = make_plan(s, df)
        order = risk.check(plan, state)
        records.append(order)
        if order.status == "approved":
            approved.append(order)
            seen.add((s.symbol, s.timeframe, s.date))
            log.info("核准:%s %s %s %s RR=%.2f size=%.4f",
                     s.track, s.symbol, s.kind, s.direction, plan.rr, order.size)
        else:
            log.info("拒絕:%s %s %s(%s)", s.track, s.symbol, s.kind, order.reject_reason)
    return approved, records


def run(mode: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    now = taipei_now()
    run_date = now.strftime("%Y-%m-%d %H:%M")
    log.info("=== 潮汐模擬倉 %s 模式,%s ===", mode, run_date)

    failures: list = []
    series: dict = {}

    if mode == "daily":
        taifex_hist = taifex.fetch_daily()
        if len(taifex_hist):
            series[(config.MTX_SYMBOL, "1d")] = taifex_hist
        fx, fx_failures = forex.fetch_daily()
        failures.extend(fx_failures)
        for (pair, tf), df in fx.items():
            series[(pair, tf)] = df
    else:  # london / newyork:小時K加掃
        fx, fx_failures = forex.fetch_hourly()
        failures.extend(fx_failures)
        for (pair, tf), df in fx.items():
            series[(pair, tf)] = df

    state = broker.load_state()
    pending = load_pending()
    cursors = load_cursors()

    closed = advance_broker(series, state, pending, cursors)
    if closed:
        broker.append_trades(closed)
        for t in closed:
            log.info("平倉:%s %s %s R=%.2f pnl=%.2f(%s)",
                     t.track, t.symbol, t.direction, t.r_multiple, t.pnl, t.exit_reason)

    signals: list[Signal] = []
    if mode == "daily":
        if (config.MTX_SYMBOL, "1d") in series:
            signals.extend(chip.detect(series[(config.MTX_SYMBOL, "1d")]))
        for pair in config.FX_PAIRS:
            if (pair, "1d") in series:
                signals.extend(technical.detect(series[(pair, "1d")], pair, "1d"))
    else:
        for pair in config.FX_PAIRS:
            if (pair, "1h") in series:
                signals.extend(technical.detect(series[(pair, "1h")], pair, "1h"))

    new_orders, _ = generate_orders(signals, series, state, pending)

    save_pending(pending + new_orders)
    save_cursors(cursors)
    broker.save_state(state, run_date)
    write_report(run_date, signals, state)
    log.info("完成:訊號 %d、新單 %d、平倉 %d、持倉 %d",
             len(signals), len(new_orders), len(closed), len(state.positions))

    if failures:
        log.error("部分資料源失敗:%s", failures)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="潮汐模擬倉管線")
    parser.add_argument("--mode", choices=["daily", "london", "newyork"],
                        default="daily")
    args = parser.parse_args()
    sys.exit(run(args.mode))
