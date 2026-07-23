# core/stats.py — F-08 績效統計與雙軌對照(AC-16)
# 指標定義(分軌計算):
#   勝率        = pnl > 0 的筆數 / 總筆數
#   平均R       = r_multiple 的算術平均
#   最大回撤    = 依平倉順序累積損益曲線(起點=虛擬資金)的最大峰谷落差 / 峰值
#   獲利因子    = 獲利筆 pnl 總和 / |虧損筆 pnl 總和|(無虧損時為 inf)
from __future__ import annotations

import math

import pandas as pd

import config


def track_metrics(ledger: pd.DataFrame, track: str) -> dict:
    rows = ledger[ledger["track"] == track]
    n = len(rows)
    if n == 0:
        return {"track": track, "trades": 0, "win_rate": None, "avg_r": None,
                "max_drawdown": None, "profit_factor": None}
    pnl = rows["pnl"].astype(float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    equity = config.CAPITAL[track] + pnl.cumsum()
    peak = equity.cummax().clip(lower=1e-9)
    max_dd = float(((peak - equity) / peak).max())

    gross_loss = float(losses.sum())
    if gross_loss == 0.0:
        pf = math.inf if float(wins.sum()) > 0 else 0.0
    else:
        pf = float(wins.sum()) / abs(gross_loss)

    return {
        "track": track,
        "trades": n,
        "win_rate": float(len(wins)) / n,
        "avg_r": float(rows["r_multiple"].astype(float).mean()),
        "max_drawdown": max_dd,
        "profit_factor": pf,
    }


def summarize(ledger: pd.DataFrame) -> dict:
    """回傳 {track: metrics} 雙軌對照。"""
    return {track: track_metrics(ledger, track) for track in sorted(config.CAPITAL)}
