# scripts/archive_round1.py — R2-06/D-09 封存切換(一次性,於 MTX 單平倉後執行)
# 前置檢查 → 記錄第一輪三檔 SHA-256 → 建議 git tag 指令。唯讀:本腳本不改任何檔案、
# 不執行 git 寫入操作;實際 tag 與 merge 由人工(或 Daniel 授權的會話)執行。
from __future__ import annotations

import hashlib
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROUND1_FILES = ["state/ledger.csv", "state/equity.csv", "state/positions.csv"]


def sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main() -> int:
    ok = True

    pos = pd.read_csv("state/positions.csv") if os.path.exists("state/positions.csv") else pd.DataFrame()
    if len(pos):
        print(f"✗ 第一輪仍有 {len(pos)} 筆未平倉(須依凍結規則自然走完,D-09):")
        print(pos.to_string(index=False))
        ok = False
    else:
        print("✓ 第一輪無未平倉部位")

    pend_path = "state/pending_orders.csv"
    if os.path.exists(pend_path):
        pend = pd.read_csv(pend_path)
        if len(pend):
            print(f"✗ 第一輪 pending_orders.csv 尚有 {len(pend)} 筆(R2-06 要求切換時為空)")
            ok = False
        else:
            print("✓ 第一輪 pending 為空")
    else:
        print("✓ 第一輪無 pending 檔")

    print("\n第一輪封存檔 SHA-256(記錄於 prereg,之後任何時點重算必須一致):")
    for p in ROUND1_FILES:
        if os.path.exists(p):
            print(f"  {p}  {sha256(p)}")
        else:
            print(f"  {p}  (不存在)")
            ok = False

    if ok:
        print("\n前置檢查全過。封存步驟(人工執行):")
        print("  git tag round1-final && git push origin round1-final")
        print("  git merge round2(或由 round2 分支開 PR)")
        print("  下次管線執行將自動以 state/r2/ 全新起算(資本重置為 config.CAPITAL)")
        return 0
    print("\n前置檢查未過,不可封存。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
