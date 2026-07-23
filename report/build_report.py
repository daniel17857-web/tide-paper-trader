# report/build_report.py — F-09 靜態報告頁(GitHub Pages,手機優先 390px,AC-18)
# 內容:今日訊號、持倉狀態、雙軌績效對照 + 固定免責聲明(交接風險提示)。
from __future__ import annotations

import html
import math
import os

import config
from core import broker, stats

KIND_NAMES = {
    "zscore": "z-score 異常", "streak": "連續買賣超", "resonance": "法人共振",
    "breakout": "突破", "pullback": "回調", "momentum": "動能",
}
TRACK_NAMES = {"chip": "籌碼軌(台指期)", "tech": "技術軌(外匯)"}
REASON_NAMES = {"stop": "停損", "target": "目標", "invalidated": "失效"}


def _fmt(value, pct: bool = False, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    if pct:
        return f"{value * 100:.1f}%"
    return f"{value:.{digits}f}"


def _signal_rows(signals: list) -> str:
    if not signals:
        return '<tr><td colspan="5" class="empty">今日無訊號</td></tr>'
    rows = []
    for s in signals:
        if s.direction == "none":
            status = "資料不足"
        elif s.suppressed:
            status = "被抑制"
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


def build_html(run_date: str, signals: list, state, metrics: dict) -> str:
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>潮汐模擬倉日報</title>
<style>
  :root {{ --ink:#1a2233; --line:#d8dee9; --muted:#5b6472; --accent:#0a5c8f; --bg:#f6f8fa; }}
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
  .disclaimer {{ margin-top:24px; padding:10px; border:1px solid var(--line); background:#fff;
                 color:var(--muted); font-size:.78rem; }}
</style>
</head>
<body>
<h1>潮汐模擬倉日報</h1>
<div class="date">資料日期:{html.escape(run_date)}(台北時間)</div>

<h2>今日訊號</h2>
<table><tr><th>軌別</th><th>標的</th><th>訊號</th><th>方向</th><th>狀態</th></tr>
{_signal_rows(signals)}</table>

<h2>持倉狀態</h2>
<table><tr><th>軌別</th><th>標的</th><th>方向</th><th>進場</th><th>停損</th><th>目標</th></tr>
{_position_rows(state.positions)}</table>

<h2>雙軌績效對照</h2>
<table>{_metrics_table(metrics)}</table>

<div class="disclaimer">
本頁為<strong>純模擬交易</strong>結果:滑價與流動性為固定假設,數據僅供雙軌訊號邏輯之相對比較,
不代表實盤可得之絕對報酬,亦不構成任何投資建議。本系統不執行任何真實下單。
</div>
</body>
</html>
"""


def write_report(run_date: str, signals: list, state, ledger=None,
                 path: str = config.REPORT_PATH) -> str:
    if ledger is None:
        ledger = broker.load_ledger()
    metrics = stats.summarize(ledger)
    html_text = build_html(run_date, signals, state, metrics)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return path
