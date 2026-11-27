# scripts/run_pipeline.py — 全管線(R2 版):
# 抓取 → 撮合(補進場/出場)→ 熔斷狀態機 → 訊號 → 計畫 → 風控 → 遙測 → 報告
# 模式:daily(台北16:30 主跑)/ london / newyork(小時K加掃)
# 跨執行狀態(R2-06):
#   state/r2/pending_orders.csv  上次執行核准、等次K進場的資格單(size=預估)
#   state/cursor.csv             各 (symbol,timeframe) 已處理到的K棒時間戳(跨輪延續)
#   state/r2/track_status.csv    各軌熔斷狀態(R2-05 A 案)
# 第一輪三檔(state/ledger.csv 等)原地凍結,本管線永不寫入。
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
from core import broker, diag, risk
from core.models import (
    TRACK_ACTIVE, TRACK_HALTED, TRACK_TERMINATED, Order, Plan, Signal,
)
from core.planner import make_plan
from report.build_report import write_report
from signals import chip, technical

log = logging.getLogger("pipeline")

PENDING_PATH = os.path.join(config.R2_STATE_DIR, "pending_orders.csv")
CURSOR_PATH = os.path.join(config.STATE_DIR, "cursor.csv")  # 跨輪延續(R2-06)
PENDING_COLUMNS = [
    "track", "symbol", "timeframe", "kind", "direction", "signal_date",
    "entry_ref", "stop", "target", "rr", "size",
]


def taipei_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


# ── pending orders 持久化(size 僅為風控預估,實際 size 成交時定,R2-01)──

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
                    stop=float(r["stop"]), target=float(r["target"]),
                    rr=float(r["rr"]) if "rr" in df.columns else 0.0)
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
                        s.date, o.plan.entry_ref, o.plan.stop, o.plan.target,
                        o.plan.rr, o.size])


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
    if "timestamp" not in df.columns:
        df = df.rename(columns={"date": "timestamp"})
    return df[["timestamp", "open", "high", "low", "close"]].copy()


def advance_broker(series: dict, state, pending: list[Order],
                   cursors: dict) -> tuple[list, list]:
    """把 cursor 之後的所有新K依時間序餵給 broker。回傳 (平倉交易, 成交層拒單)。"""
    events: dict = {}
    for key, df in series.items():
        bars = _bars_df(key, df)
        cursor = cursors.get(key)
        if cursor is None:
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

    closed_all, rejects_all = [], []
    for ts in sorted(events):
        bars = events[ts]
        fillable, still_pending = [], []
        for o in pending:
            sig = o.plan.signal
            key = (sig.symbol, sig.timeframe)
            if key not in bars or str(sig.date) >= ts:
                still_pending.append(o)
                continue
            # R2-05:成交前複檢軌道狀態——核准後才熔斷/終止的軌,pending 單
            # 不得進場(消耗該單並記拒單),否則冷卻解除條件(無持倉)也會被卡
            status = state.status_of(sig.track).status
            if status == TRACK_TERMINATED:
                rejects_all.append({"date": ts, "track": sig.track,
                                    "symbol": sig.symbol, "kind": sig.kind,
                                    "timeframe": sig.timeframe,
                                    "reason": "track_terminated"})
                continue
            if status == TRACK_HALTED:
                rejects_all.append({"date": ts, "track": sig.track,
                                    "symbol": sig.symbol, "kind": sig.kind,
                                    "timeframe": sig.timeframe,
                                    "reason": "drawdown_halt"})
                continue
            fillable.append(o)   # 訊號次一根K:試單一次,成敗皆消耗
        state, closed, frej = broker.on_new_bar(state, bars, fillable)
        pending[:] = still_pending
        closed_all.extend(closed)
        rejects_all.extend(frej)
        # 逐K推進中可能觸熔斷(平倉更新權益後),立即更新狀態,
        # 使同批後續K的 pending 單在新狀態下被攔截
        update_halt_state(state, ts, as_of=ts)
    return closed_all, rejects_all


# ── 熔斷狀態機(R2-05 A 案)──

def _track_trading_dates(track: str) -> list[str]:
    """該軌自身資料日曆的K棒「日」清單(升冪、去重)。chip=台指期;tech=四FX日K聯集。"""
    dates: set = set()
    if track == "chip":
        hist = taifex.load_history()
        dates.update(str(d)[:10] for d in hist["date"].astype(str))
    else:
        for pair in config.FX_PAIRS:
            hist = forex.load_history(pair, "1d")
            dates.update(str(t)[:10] for t in hist["timestamp"].astype(str))
    return sorted(dates)


def update_halt_state(state, run_date: str, as_of: str | None = None) -> None:
    """記錄新熔斷事件、處理冷卻解除與終止。零隨機。
    advance_broker 逐K呼叫時 run_date = 該K時間戳,故 halt_date 即觸發K的
    K棒日(TrackStatus 語義);as_of 限制冷卻計數只看 ≤ 該日的K棒日,
    防止 catch-up 重播舊K時用到磁碟上更晚的日期。"""
    cutoff = (as_of or run_date)[:10]
    for track in sorted(config.CAPITAL):
        ts = state.status_of(track)
        if ts.status == TRACK_TERMINATED:
            continue
        if ts.status == TRACK_ACTIVE and state.drawdown(track) >= config.DRAWDOWN_HALT:
            if ts.resets >= config.HALT_MAX_RESETS:   # 第 3 次熔斷 → 終止取樣
                ts.status = TRACK_TERMINATED
                ts.halt_date = run_date[:10]
                broker.append_halt({"date": run_date[:10], "track": track,
                                    "equity": round(state.equity[track], 2),
                                    "peak": round(state.peak_equity[track], 2),
                                    "reset_no": ts.resets})
                log.warning("%s 軌第 %d 次熔斷 → 終止取樣(track_terminated)",
                            track, ts.resets + 1)
            else:
                ts.status = TRACK_HALTED
                ts.halt_date = run_date[:10]
                broker.append_halt({"date": run_date[:10], "track": track,
                                    "equity": round(state.equity[track], 2),
                                    "peak": round(state.peak_equity[track], 2),
                                    "reset_no": ts.resets})
                log.warning("%s 軌觸發熔斷(回撤 %.2f%%),進入冷卻",
                            track, state.drawdown(track) * 100)
        elif ts.status == TRACK_HALTED:
            # 冷卻解除:滿 HALT_COOLDOWN_DAYS 個該軌交易日,且該軌無持倉(D-01)
            elapsed = [d for d in _track_trading_dates(track)
                       if ts.halt_date < d <= cutoff]
            if (len(elapsed) >= config.HALT_COOLDOWN_DAYS
                    and state.open_count(track) == 0):
                ts.status = TRACK_ACTIVE
                ts.resets += 1
                state.peak_equity[track] = state.equity[track]  # peak 重置
                log.warning("%s 軌冷卻期滿解除熔斷(第 %d 次重置),peak 重置為 %.2f",
                            track, ts.resets, state.equity[track])


def generate_orders(signals: list[Signal], series: dict, state,
                    pending: list[Order] | None = None,
                    ) -> tuple[list[Order], list[dict], dict]:
    """訊號 → 計畫 → 風控。回傳 (核准單, 拒單紀錄, 遙測 outcomes)。
    冪等保護:pending 已有同 (symbol, timeframe, 訊號日) 的單不重複下(重跑安全)。"""
    seen = {(o.plan.signal.symbol, o.plan.signal.timeframe, o.plan.signal.date)
            for o in (pending or [])}
    approved, rejects, outcomes = [], [], {}
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
        # pending(上次遺留)+ 本次已核准者皆視同持倉納入曝險檢核(R2 審查修正)
        order = risk.check(plan, state, pending=(pending or []) + approved)
        if order.status == "approved":
            approved.append(order)
            seen.add((s.symbol, s.timeframe, s.date))
            outcomes[(s.symbol, s.timeframe, s.kind)] = "approved"
            log.info("核准:%s %s %s %s RR=%.2f 預估size=%.4f",
                     s.track, s.symbol, s.kind, s.direction, plan.rr, order.size)
        else:
            reason = order.reject_reason
            reason = reason[5:] if reason.startswith("plan_") else reason
            rejects.append({"date": s.date, "track": s.track, "symbol": s.symbol,
                            "kind": s.kind, "timeframe": s.timeframe,
                            "reason": reason})
            outcomes[(s.symbol, s.timeframe, s.kind)] = reason
            log.info("拒絕:%s %s %s(%s)", s.track, s.symbol, s.kind, reason)
    return approved, rejects, outcomes


def run(mode: str) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    now = taipei_now()
    run_date = now.strftime("%Y-%m-%d %H:%M")
    log.info("=== 潮汐模擬倉(第二輪)%s 模式,%s ===", mode, run_date)

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

    closed, fill_rejects = advance_broker(series, state, pending, cursors)
    if closed:
        broker.append_trades(closed)
        for t in closed:
            log.info("平倉:%s %s %s R=%.2f pnl=%.2f(%s)",
                     t.track, t.symbol, t.direction, t.r_multiple, t.pnl, t.exit_reason)

    update_halt_state(state, run_date)

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

    new_orders, plan_rejects, outcomes = generate_orders(signals, series, state, pending)

    # R2-08 遙測落地(失敗不得中斷主管線)
    try:
        broker.append_rejects(fill_rejects + plan_rejects)
    except Exception as exc:  # noqa: BLE001
        log.error("rejects.csv 寫入失敗(不中斷):%s", exc)
    try:
        rows = []
        if mode == "daily" and (config.MTX_SYMBOL, "1d") in series:
            rows += diag.chip_rows(series[(config.MTX_SYMBOL, "1d")])
        tfs = ["1d"] if mode == "daily" else ["1h"]
        for pair in config.FX_PAIRS:
            for tf in tfs:
                if (pair, tf) in series:
                    rows += diag.tech_rows(series[(pair, tf)], pair, tf)
        rows += diag.signal_rows(signals, outcomes)
        diag.append(rows)
    except Exception as exc:  # noqa: BLE001
        log.error("signal_diag.csv 寫入失敗(不中斷):%s", exc)

    save_pending(pending + new_orders)
    save_cursors(cursors)
    broker.save_state(state, run_date)
    write_report(run_date, signals, state, outcomes=outcomes)
    log.info("完成:訊號 %d、新單 %d、平倉 %d、持倉 %d",
             len(signals), len(new_orders), len(closed), len(state.positions))

    if failures:
        log.error("部分資料源失敗:%s", failures)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="潮汐模擬倉管線(第二輪)")
    parser.add_argument("--mode", choices=["daily", "london", "newyork"],
                        default="daily")
    args = parser.parse_args()
    sys.exit(run(args.mode))
