# report/build_report.py — F-09 靜態報告頁(R2-06/R2-08 改版,手機優先 390px,AC-18)
# 內容:管線心跳、各軌風控狀態、今日訊號(含風控註記)、持倉、第二輪績效、
#       第一輪封存區塊(不可比標註)、最近 30 日拒單統計 + 固定免責聲明。
from __future__ import annotations

import html
import math
import os

import pandas as pd

import config
from core import broker, stats
from core.models import TRACK_HALTED, TRACK_TERMINATED

ROUND1_LEDGER = os.path.join(config.STATE_DIR, "ledger.csv")

KIND_NAMES = {
    "zscore": "z-score 異常", "streak": "連續買賣超", "resonance": "法人共振",
    "breakout": "突破", "pullback": "回調", "momentum": "動能",
}
TRACK_NAMES = {"chip": "籌碼軌(台指期)", "tech": "技術軌(外匯)"}
STATUS_NAMES = {"active": "正常", "halted": "熔斷冷卻中", "terminated": "已終止取樣"}


def _fmt(value, pct: bool = False, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    if pct:
        return f"{value * 100:.1f}%"
    return f"{value:.{digits}f}"


def _signal_rows(signals: list, outcomes: dict) -> str:
    if not signals:
        return '<tr><td colspan="5" class="empty">今日無訊號</td></tr>'
    rows = []
    for s in signals:
        if s.direction == "none":
            status = "資料不足"
        elif s.suppressed:
            status = "被抑制"
        else:
            out = outcomes.get((s.symbol, s.timeframe, s.kind))
            if out == "approved":
                status = "有效(已核准)"
            elif out:
                status = f"訊號有效,風控拒絕({html.escape(out)})"
            else:
                status = "有效"
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                html.escape(TRACK_NAMES.get(s.track, s.track)),
                html.escape(s.symbol),
                html.escape(KIND_NAMES.get(s.kind, s.kind)),
                {"long": "多", "short": "空", "none": "—"}.get(s.direction, s.direction),
                status,
            ))
    return "".join(rows)


def _position_rows(positions: list) -> str:
    if not positions:
        return '<tr><td colspan="6" class="empty">目前無持倉</td></tr>'
    rows = []
    for p in positions:
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%.5g</td><td>%.5g</td><td>%.5g</td></tr>" % (
                html.escape(TRACK_NAMES.get(p.track, p.track)),
                html.escape(p.symbol),
                "多" if p.direction == "long" else "空",
                p.entry_price, p.stop, p.target,
            ))
    return "".join(rows)


def _metrics_table(metrics: dict) -> str:
    head = "<tr><th>指標</th>" + "".join(
        f"<th>{html.escape(TRACK_NAMES[t])}</th>" for t in sorted(metrics)) + "</tr>"
    rows = [
        ("已平倉筆數", [str(metrics[t]["trades"]) for t in sorted(metrics)]),
        ("勝率", [_fmt(metrics[t]["win_rate"], pct=True) for t in sorted(metrics)]),
        ("平均 R", [_fmt(metrics[t]["avg_r"]) for t in sorted(metrics)]),
        ("最大回撤", [_fmt(metrics[t]["max_drawdown"], pct=True) for t in sorted(metrics)]),
        ("獲利因子", [_fmt(metrics[t]["profit_factor"]) for t in sorted(metrics)]),
    ]
    body = "".join(
        "<tr><td>%s</td>%s</tr>" % (name, "".join(f"<td>{v}</td>" for v in vals))
        for name, vals in rows)
    return head + body


def _risk_status_rows(state, zero_fill_streaks: dict) -> str:
    rows = []
    for track in sorted(config.CAPITAL):
        ts = state.status_of(track)
        status = STATUS_NAMES.get(ts.status, ts.status)
        extra = []
        if ts.status in (TRACK_HALTED, TRACK_TERMINATED) and ts.halt_date:
            extra.append(f"自 {html.escape(ts.halt_date)}")
        if ts.resets:
            extra.append(f"已重置 {ts.resets} 次")
        dd = state.drawdown(track)
        extra.append(f"分段回撤 {_fmt(dd, pct=True)}")
        total_dd = 1.0 - state.equity.get(track, config.CAPITAL[track]) / config.CAPITAL[track]
        extra.append(f"總回撤 {_fmt(max(total_dd, 0.0), pct=True)}")
        streak = zero_fill_streaks.get(track, 0)
        warn = f'<span class="warn">連續 {streak} 交易日零成交</span>' if streak >= 10 \
            else (f"連續 {streak} 交易日零成交" if streak > 0 else "")
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            html.escape(TRACK_NAMES[track]), status, ";".join(extra), warn))
    return rows and "".join(rows) or ""


def _reject_summary_rows(run_date: str) -> str:
    path = broker.REJECTS_PATH
    if not os.path.exists(path):
        return '<tr><td colspan="3" class="empty">尚無拒單紀錄</td></tr>'
    df = pd.read_csv(path, dtype=str)
    cutoff = (pd.Timestamp(run_date[:10]) - pd.Timedelta(days=30)).date().isoformat()
    df = df[df["date"].str[:10] >= cutoff]
    if len(df) == 0:
        return '<tr><td colspan="3" class="empty">最近 30 日無拒單</td></tr>'
    counts = df.groupby(["track", "reason"]).size().reset_index(name="n")
    counts = counts.sort_values(["track", "n"], ascending=[True, False])
    return "".join(
        "<tr><td>%s</td><td>%s</td><td>%d</td></tr>" % (
            html.escape(TRACK_NAMES.get(r["track"], r["track"])),
            html.escape(r["reason"]), int(r["n"]))
        for _, r in counts.iterrows())


def _equity_gap_note(run_date: str) -> str:
    path = broker.EQUITY_PATH
    if not os.path.exists(path):
        return ""
    df = pd.read_csv(path, dtype={"date": str})
    days = sorted({d[:10] for d in df["date"].astype(str)})
    gaps = []
    for a, b in zip(days, days[1:]):
        if (pd.Timestamp(b) - pd.Timestamp(a)).days > 3:
            gaps.append(f"{a}→{b}")
    if not gaps:
        return ""
    return ('<div class="warnbox">權益快照出現 &gt;3 日空洞:%s(檢查排程)</div>'
            % html.escape(";".join(gaps[-3:])))


def _round1_block() -> str:
    if not os.path.exists(ROUND1_LEDGER):
        return ""
    ledger = pd.read_csv(ROUND1_LEDGER, dtype={"track": str})
    metrics = stats.summarize(ledger)
    return f"""
<h2>第一輪(已封存)</h2>
<div class="note">第一輪評估期自 2026-07-23 起,封存於 round1-final;下表由封存台帳計算。
機制已於第二輪變更(sizing 基準、R 刻度、取樣框),<strong>兩輪指標不可直接比較</strong>,
永不宣稱「第二輪比第一輪進步」。第二輪資本已重置,第一輪損益不帶入、僅紀錄於此。</div>
<table>{_metrics_table(metrics)}</table>
"""


def build_html(run_date: str, signals: list, state, metrics: dict,
               outcomes: dict | None = None,
               zero_fill_streaks: dict | None = None) -> str:
    outcomes = outcomes or {}
    zero_fill_streaks = zero_fill_streaks or {}
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>潮汐模擬倉日報</title>
<style>
  :root {{ --ink:#1a2233; --line:#d8dee9; --muted:#5b6472; --accent:#0a5c8f; --bg:#f6f8fa;
           --warn:#a33a00; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:12px; font-family:-apple-system,"PingFang TC","Noto Sans TC",sans-serif;
         color:var(--ink); background:var(--bg); font-size:15px; line-height:1.5; }}
  h1 {{ font-size:1.25rem; margin:4px 0 2px; }}
  h2 {{ font-size:1.05rem; margin:20px 0 8px; color:var(--accent); }}
  .date {{ color:var(--muted); font-size:.85rem; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; font-size:.85rem;
           table-layout:fixed; word-break:break-word; }}
  th,td {{ border:1px solid var(--line); padding:6px 4px; text-align:center; }}
  th {{ background:#eef2f6; font-weight:600; }}
  .empty {{ color:var(--muted); }}
  .warn {{ color:var(--warn); font-weight:700; }}
  .note {{ color:var(--muted); font-size:.78rem; margin:4px 0 8px; }}
  .warnbox {{ margin:8px 0; padding:8px; border:1px solid var(--warn); color:var(--warn);
              background:#fff; font-size:.82rem; }}
  .disclaimer {{ margin-top:24px; padding:10px; border:1px solid var(--line); background:#fff;
                 color:var(--muted); font-size:.78rem; }}
</style>
</head>
<body>
<h1>潮汐模擬倉日報(第二輪)</h1>
<div class="date">最近執行:{html.escape(run_date)}(台北時間;資料新鮮度另見下方快照空洞檢查)</div>
{_equity_gap_note(run_date)}

<h2>各軌風控狀態</h2>
<table><tr><th>軌別</th><th>狀態</th><th>回撤(分段/總)</th><th>成交警示</th></tr>
{_risk_status_rows(state, zero_fill_streaks)}</table>

<h2>今日訊號</h2>
<table><tr><th>軌別</th><th>標的</th><th>訊號</th><th>方向</th><th>狀態</th></tr>
{_signal_rows(signals, outcomes)}</table>

<h2>持倉狀態</h2>
<table><tr><th>軌別</th><th>標的</th><th>方向</th><th>進場</th><th>停損</th><th>目標</th></tr>
{_position_rows(state.positions)}</table>

<h2>雙軌績效對照(第二輪)</h2>
<table>{_metrics_table(metrics)}</table>

<h2>最近 30 日拒單統計</h2>
<table><tr><th>軌別</th><th>原因</th><th>筆數</th></tr>
{_reject_summary_rows(run_date)}</table>
{_round1_block()}

<div class="disclaimer">
本頁為<strong>純模擬交易</strong>結果:滑價與流動性為固定假設,數據僅供雙軌訊號邏輯之相對比較,
不代表實盤可得之絕對報酬,亦不構成任何投資建議。本系統不執行任何真實下單。
第二輪統計自歸零日起算,與第一輪不可比較。
</div>
</body>
</html>
"""


def _zero_fill_streaks(run_date: str, ledger: pd.DataFrame) -> dict:
    """各軌連續零成交「交易日」數(以該軌 equity 快照日近似交易日曆)。"""
    eq_path = broker.EQUITY_PATH
    if not os.path.exists(eq_path):
        return {}
    eq = pd.read_csv(eq_path, dtype={"date": str, "track": str})
    out = {}
    for track in config.CAPITAL:
        days = sorted({d[:10] for d in eq[eq["track"] == track]["date"].astype(str)})
        if not days:
            continue
        fills = set()
        rows = ledger[ledger["track"] == track] if len(ledger) else ledger
        if len(rows):
            fills = {d[:10] for d in rows["entry_date"].astype(str)} | \
                    {d[:10] for d in rows["exit_date"].astype(str)}
        streak = 0
        for d in reversed(days):
            if d in fills:
                break
            streak += 1
        out[track] = streak
    return out


def write_report(run_date: str, signals: list, state, ledger=None,
                 path: str = config.REPORT_PATH, outcomes: dict | None = None) -> str:
    if ledger is None:
        ledger = broker.load_ledger()
    metrics = stats.summarize(ledger)
    html_text = build_html(run_date, signals, state, metrics, outcomes=outcomes,
                           zero_fill_streaks=_zero_fill_streaks(run_date, ledger))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return path
