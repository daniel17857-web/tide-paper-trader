# scripts/diagnose_zero_trigger.py — R2-09 零觸發調查(唯讀診斷)
# 目的:查明 chip/resonance 與 tech/momentum 第一輪 8 週 0 筆成交屬
#       (A) bug / (B) 門檻結構性過嚴 / (C) 觸發但被優先序或風控抑制。
# 鐵律:唯讀——只讀 data/ 與 config/signals 既有程式;除 stdout 外不寫任何東西。
# 零隨機性:固定排序、固定浮點位數,重跑輸出必須完全一致。
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config
from signals import chip, technical

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")

HALT_DATE = "2026-09-01"  # 技術軌 10% 回撤熔斷日(此日後技術軌新倉一律被風控拒絕)
TIMEFRAMES = ["1d", "1h"]
CHIP_KINDS = list(config.CHIP_PRIORITY)    # resonance, zscore, streak
TECH_KINDS = list(config.TECH_PRIORITY)    # breakout, momentum, pullback


def f4(x: float) -> str:
    return f"{x:.4f}"


def load_taifex() -> pd.DataFrame:
    return pd.read_csv(os.path.join(DATA_DIR, "taifex_history.csv"),
                       dtype={"date": str})


def load_fx(pair: str, tf: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(DATA_DIR, f"fx_{pair}_{tf}.csv"),
                       dtype={"timestamp": str})


def tally(signals: list) -> dict:
    """依 kind 分層計數:raw(含被抑制)、live、suppressed、suppressed_by 分布、標記訊號。"""
    out: dict = {}
    for s in signals:
        d = out.setdefault(s.kind, {"raw": 0, "live": 0, "suppressed": 0,
                                    "by": {}, "markers": 0})
        if s.direction not in ("long", "short"):
            d["markers"] += 1
            continue
        d["raw"] += 1
        if s.suppressed:
            d["suppressed"] += 1
            by = str(s.meta.get("suppressed_by", "?"))
            d["by"][by] = d["by"].get(by, 0) + 1
        else:
            d["live"] += 1
    return out


def fmt_by(by: dict) -> str:
    if not by:
        return "-"
    return ",".join(f"{k}:{v}" for k, v in sorted(by.items()))


def main() -> None:
    print("=" * 78)
    print("R2-09 零觸發調查報告(唯讀診斷;資料=第一輪 data/ 全史)")
    print("=" * 78)

    taifex = load_taifex()
    fx = {(p, tf): load_fx(p, tf) for p in config.FX_PAIRS for tf in TIMEFRAMES}

    print(f"資料範圍 taifex_history: {taifex['date'].iloc[0]} ~ "
          f"{taifex['date'].iloc[-1]}({len(taifex)} 日)")
    for p in config.FX_PAIRS:
        for tf in TIMEFRAMES:
            df = fx[(p, tf)]
            print(f"資料範圍 fx_{p}_{tf}: {df['timestamp'].iloc[0]} ~ "
                  f"{df['timestamp'].iloc[-1]}({len(df)} 根)")

    # ── 第 1 節:detect_range 全史重播,六個 (track,kind) 分層計數 ──────────
    print()
    print("─" * 78)
    print("[1] 分層計數(detect_range 全史重播;raw=含被抑制之觸發)")
    print("─" * 78)

    chip_tally = tally(chip.detect_range(taifex))
    tech_per_series: dict = {}
    for p in config.FX_PAIRS:
        for tf in TIMEFRAMES:
            tech_per_series[(p, tf)] = tally(
                technical.detect_range(fx[(p, tf)], p, tf))

    tech_tally: dict = {}
    for k in TECH_KINDS:
        agg = {"raw": 0, "live": 0, "suppressed": 0, "by": {}, "markers": 0}
        for key in tech_per_series:
            d = tech_per_series[key].get(k)
            if d is None:
                continue
            agg["raw"] += d["raw"]
            agg["live"] += d["live"]
            agg["suppressed"] += d["suppressed"]
            agg["markers"] += d["markers"]
            for b, v in sorted(d["by"].items()):
                agg["by"][b] = agg["by"].get(b, 0) + v
        tech_tally[k] = agg

    hdr = (f"{'track':<6}{'kind':<11}{'raw觸發':>8}{'live':>7}{'被抑制':>8}"
           f"  suppressed_by 分布")
    print(hdr)
    print("-" * 78)
    for k in CHIP_KINDS:
        d = chip_tally.get(k, {"raw": 0, "live": 0, "suppressed": 0, "by": {}})
        print(f"{'chip':<6}{k:<11}{d['raw']:>10}{d['live']:>7}"
              f"{d['suppressed']:>10}  {fmt_by(d['by'])}")
    for k in TECH_KINDS:
        d = tech_tally[k]
        print(f"{'tech':<6}{k:<11}{d['raw']:>10}{d['live']:>7}"
              f"{d['suppressed']:>10}  {fmt_by(d['by'])}")
    zmark = chip_tally.get("zscore", {}).get("markers", 0)
    print(f"(註:chip/zscore 另有 insufficient_data 標記訊號 {zmark} 筆,"
          f"direction=none,不計入 raw)")

    print()
    print("tech 各序列明細(pair,timeframe × kind):")
    print(f"{'pair':<8}{'tf':<4}{'kind':<11}{'raw':>5}{'live':>6}{'被抑制':>8}"
          f"  suppressed_by")
    print("-" * 78)
    for p in config.FX_PAIRS:
        for tf in TIMEFRAMES:
            for k in TECH_KINDS:
                d = tech_per_series[(p, tf)].get(
                    k, {"raw": 0, "live": 0, "suppressed": 0, "by": {}})
                print(f"{p:<8}{tf:<4}{k:<11}{d['raw']:>5}{d['live']:>6}"
                      f"{d['suppressed']:>10}  {fmt_by(d['by'])}")

    # ── 第 2 節:resonance 四子條件拆解 ────────────────────────────────────
    print()
    print("─" * 78)
    print("[2] resonance 四子條件拆解(逐日,需前一日 ⇒ 可評估天數 = N-1)")
    print("─" * 78)

    f = taifex["foreign_net"].astype(float)
    t = taifex["trust_net"].astype(float)
    n = len(taifex)
    days = n - 1  # i = 1 .. n-1

    # 長邊:f>0, t>0, f較前日增, t較前日增;短邊鏡像
    cnt = {k: 0 for k in [
        "L_f_pos", "L_t_pos", "L_f_up", "L_t_up",
        "L_sign", "L_sign_fup", "L_all4",
        "S_f_neg", "S_t_neg", "S_f_dn", "S_t_dn",
        "S_sign", "S_sign_fdn", "S_all4",
        "both_pos", "both_neg", "opp_sign", "any_zero",
    ]}
    for i in range(1, n):
        fi, fp = float(f.iloc[i]), float(f.iloc[i - 1])
        ti, tp = float(t.iloc[i]), float(t.iloc[i - 1])
        # 同號統計
        if fi > 0 and ti > 0:
            cnt["both_pos"] += 1
        elif fi < 0 and ti < 0:
            cnt["both_neg"] += 1
        elif fi == 0 or ti == 0:
            cnt["any_zero"] += 1
        else:
            cnt["opp_sign"] += 1
        # 長邊四子條件
        c1, c2, c3, c4 = fi > 0, ti > 0, fi > fp, ti > tp
        cnt["L_f_pos"] += c1
        cnt["L_t_pos"] += c2
        cnt["L_f_up"] += c3
        cnt["L_t_up"] += c4
        cnt["L_sign"] += c1 and c2
        cnt["L_sign_fup"] += c1 and c2 and c3
        cnt["L_all4"] += c1 and c2 and c3 and c4
        # 短邊四子條件
        d1, d2, d3, d4 = fi < 0, ti < 0, fi < fp, ti < tp
        cnt["S_f_neg"] += d1
        cnt["S_t_neg"] += d2
        cnt["S_f_dn"] += d3
        cnt["S_t_dn"] += d4
        cnt["S_sign"] += d1 and d2
        cnt["S_sign_fdn"] += d1 and d2 and d3
        cnt["S_all4"] += d1 and d2 and d3 and d4

    print(f"可評估天數:{days}")
    print()
    print("長邊(resonance long:f>0 ∧ t>0 ∧ f↑ ∧ t↑):")
    print(f"  f_today>0            :{cnt['L_f_pos']:>4} 天")
    print(f"  t_today>0            :{cnt['L_t_pos']:>4} 天")
    print(f"  f 較前日增(f↑)      :{cnt['L_f_up']:>4} 天")
    print(f"  t 較前日增(t↑)      :{cnt['L_t_up']:>4} 天")
    print(f"  交集 f>0∧t>0         :{cnt['L_sign']:>4} 天")
    print(f"  交集 f>0∧t>0∧f↑      :{cnt['L_sign_fup']:>4} 天")
    print(f"  交集 全四條件        :{cnt['L_all4']:>4} 天")
    print()
    print("短邊(resonance short:f<0 ∧ t<0 ∧ f↓ ∧ t↓):")
    print(f"  f_today<0            :{cnt['S_f_neg']:>4} 天")
    print(f"  t_today<0            :{cnt['S_t_neg']:>4} 天")
    print(f"  f 較前日減(f↓)      :{cnt['S_f_dn']:>4} 天")
    print(f"  t 較前日減(t↓)      :{cnt['S_t_dn']:>4} 天")
    print(f"  交集 f<0∧t<0         :{cnt['S_sign']:>4} 天")
    print(f"  交集 f<0∧t<0∧f↓      :{cnt['S_sign_fdn']:>4} 天")
    print(f"  交集 全四條件        :{cnt['S_all4']:>4} 天")
    print()
    same = cnt["both_pos"] + cnt["both_neg"]
    print(f"外資/投信淨額同號統計(可評估 {days} 天):")
    print(f"  同號(皆>0):{cnt['both_pos']} 天;同號(皆<0):{cnt['both_neg']} 天;"
          f"同號合計:{same} 天")
    print(f"  異號:{cnt['opp_sign']} 天;任一為 0:{cnt['any_zero']} 天")
    f_pos_all = int((f > 0).sum()); f_neg_all = int((f < 0).sum())
    t_pos_all = int((t > 0).sum()); t_neg_all = int((t < 0).sum())
    print(f"  全史 {n} 天符號:foreign_net >0:{f_pos_all} / <0:{f_neg_all} / "
          f"=0:{n - f_pos_all - f_neg_all - int(f.isna().sum())}"
          f" / 缺值:{int(f.isna().sum())}")
    print(f"  全史 {n} 天符號:trust_net   >0:{t_pos_all} / <0:{t_neg_all} / "
          f"=0:{n - t_pos_all - t_neg_all - int(t.isna().sum())}"
          f" / 缺值:{int(t.isna().sum())}")
    t_zero = int((t == 0).sum()); t_nan = int(t.isna().sum())
    print(f"  trust_net 零值比例:{t_zero}/{n} = {f4(t_zero / n)};"
          f"缺值比例:{t_nan}/{n} = {f4(t_nan / n)}")
    print(f"  foreign_net 值域:[{f4(float(f.min()))}, {f4(float(f.max()))}];"
          f"trust_net 值域:[{f4(float(t.min()))}, {f4(float(t.max()))}]")
    print("  (資料語意:adapters/taifex.py 取 TAIFEX OpenInterest(Net) 淨未平倉口數,"
          "非當日淨買賣)")

    # ── 第 3 節:momentum 假說檢驗(向量化重算,與第 1 節重播交叉驗證)──────
    print()
    print("─" * 78)
    print("[3] momentum 假說檢驗(逐根;僅計可評估根 n≥BREAKOUT_WINDOW+1,"
          "與 detect 同門檻)")
    print("─" * 78)
    print(f"{'pair':<8}{'tf':<4}{'可評估':>7}{'大實體':>7}{'其中順勢':>9}"
          f"{'順勢∧同根breakout':>17}{'純momentum(live)':>17}{'交叉驗證':>9}")
    print("-" * 78)

    tot = {"eval": 0, "big": 0, "aligned": 0, "supp": 0, "live": 0}
    live_rows: list = []
    for p in config.FX_PAIRS:
        for tf in TIMEFRAMES:
            df = fx[(p, tf)]
            o = df["open"].astype(float)
            h = df["high"].astype(float)
            lo = df["low"].astype(float)
            c = df["close"].astype(float)
            # 與 signals/technical.py 完全同式(ewm/rolling 皆因果,逐前綴等值)
            ema20 = c.ewm(span=config.EMA_WINDOW, adjust=False).mean()
            trend_up = ema20 > ema20.shift(config.EMA_TREND_LOOKBACK)
            trend_dn = ema20 < ema20.shift(config.EMA_TREND_LOOKBACK)
            prev_c = c.shift(1)
            tr = pd.concat([h - lo, (h - prev_c).abs(),
                            (lo - prev_c).abs()], axis=1).max(axis=1)
            atr14 = tr.rolling(config.ATR_WINDOW).mean()
            body = (c - o).abs()
            big = atr14.notna() & (atr14 > 0) & (body >= config.MOMENTUM_ATR_MULT * atr14)
            aligned = big & (((c > o) & trend_up) | ((c < o) & trend_dn))
            hh = h.shift(1).rolling(config.BREAKOUT_WINDOW).max()
            ll = lo.shift(1).rolling(config.BREAKOUT_WINDOW).min()
            brk = (c > hh) | (c < ll)
            evaluable = pd.Series(df.index >= config.BREAKOUT_WINDOW,
                                  index=df.index)
            big_e = big & evaluable
            ali_e = aligned & evaluable
            supp_e = ali_e & brk
            live_e = ali_e & ~brk

            n_eval = int(evaluable.sum())
            n_big = int(big_e.sum())
            n_ali = int(ali_e.sum())
            n_supp = int(supp_e.sum())
            n_live = int(live_e.sum())

            # 交叉驗證:detect_range 重播的 momentum raw / suppressed_by breakout / live
            d = tech_per_series[(p, tf)].get(
                "momentum", {"raw": 0, "live": 0, "suppressed": 0, "by": {}})
            ok = (d["raw"] == n_ali and d["live"] == n_live
                  and d["by"].get("breakout", 0) == n_supp)
            tag = "OK" if ok else "MISMATCH"

            print(f"{p:<8}{tf:<4}{n_eval:>7}{n_big:>8}{n_ali:>11}"
                  f"{n_supp:>18}{n_live:>18}{tag:>10}")
            tot["eval"] += n_eval; tot["big"] += n_big; tot["aligned"] += n_ali
            tot["supp"] += n_supp; tot["live"] += n_live

            for ts in df.loc[live_e, "timestamp"].astype(str):
                live_rows.append((p, tf, ts))

    print("-" * 78)
    print(f"{'合計':<12}{tot['eval']:>7}{tot['big']:>8}{tot['aligned']:>11}"
          f"{tot['supp']:>18}{tot['live']:>18}")
    print(f"(大實體={int(tot['big'])} 根中,順勢僅 {tot['aligned']} 根;"
          f"逆勢/盤整不觸發 momentum,佔 {tot['big'] - tot['aligned']} 根)")

    print()
    print(f"live momentum 訊號逐筆(共 {len(live_rows)} 筆;熔斷日 {HALT_DATE},"
          f"其後技術軌新倉一律被風控拒絕):")
    pre = post = 0
    for p, tf, ts in sorted(live_rows, key=lambda r: (r[2], r[0], r[1])):
        day = ts[:10]
        flag = "熔斷前" if day < HALT_DATE else "熔斷後(不可成交)"
        if day < HALT_DATE:
            pre += 1
        else:
            post += 1
        print(f"  {p:<8}{tf:<4}{ts:<26}{flag}")
    print(f"  小計:熔斷前({HALT_DATE} 之前){pre} 筆;"
          f"{HALT_DATE} 當日及之後 {post} 筆")

    # ── 第 4 節:事實摘要(純計數,判讀見調查回報)────────────────────────
    print()
    print("─" * 78)
    print("[4] 事實摘要")
    print("─" * 78)
    res = chip_tally.get("resonance", {"raw": 0, "live": 0, "suppressed": 0})
    mom_raw = tech_tally["momentum"]["raw"]
    mom_live = tech_tally["momentum"]["live"]
    mom_supp = tech_tally["momentum"]["suppressed"]
    print(f"resonance:raw={res['raw']}, live={res['live']}, "
          f"suppressed={res['suppressed']};同號天數={same}/{days}")
    print(f"momentum :raw={mom_raw}, live={mom_live}, suppressed={mom_supp}"
          f"(suppressed_by={fmt_by(tech_tally['momentum']['by'])});"
          f"live 中熔斷前 {pre} 筆 / 熔斷後 {post} 筆")
    print("=" * 78)


if __name__ == "__main__":
    main()
