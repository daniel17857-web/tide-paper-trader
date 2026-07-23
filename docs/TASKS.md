# TASKS.md — 潮汐模擬倉(tide-paper-trader)

> **一句話結論**:共 11 個任務,建議順序 T-01 → T-11,關鍵路徑為 T-01 → T-04 → T-06 → T-07 → T-08 → T-09 → T-11(籌碼軌先通,技術軌平行補上)。

## 任務規則

- 每個任務 ≤ 半天、可獨立驗證、明確對應 AC 編號
- 狀態:⬜ 未開始 / 🔨 開發中 / 🧪 驗證中 / ✅ 主席已驗收 / ❌ 退回

## 任務清單

### T-01 repo 骨架 + 資料模型 + verify.sh
- 對應:ARCH §3 / 非功能需求(型別、可重現)
- 依賴:無
- 產出:目錄結構、`core/models.py`(Signal/Plan/Order/Trade dataclass)、`config.py`、pytest 骨架、`scripts/verify.sh`
- 驗證方式:自動測試(dataclass 欄位斷言)+ 盲審
- 狀態:⬜

### T-02 TAIFEX adapter
- 對應:F-01 / AC-01, AC-02
- 依賴:T-01
- 產出:`adapters/taifex.py` + 歷史檔累積邏輯
- 驗證方式:自動測試(mock 回應 + 去重斷言)
- 狀態:⬜

### T-03 外匯 adapter
- 對應:F-02 / AC-03, AC-04
- 依賴:T-01
- 產出:`adapters/forex.py`(日K + 小時K,四貨幣對)
- 驗證方式:自動測試(mock + 部分失敗情境)
- 狀態:⬜

### T-04 籌碼軌訊號引擎
- 對應:F-03 / AC-05, AC-06
- 依賴:T-01(fixture 可先於 T-02 用假資料)
- 產出:`signals/chip.py` + `tests/fixtures/chip_case.csv`(含人工驗算答案)
- 驗證方式:自動測試(fixture 逐日比對)
- 狀態:⬜

### T-05 技術軌訊號引擎
- 對應:F-04 / AC-07, AC-08
- 依賴:T-01
- 產出:`signals/technical.py` + fixture(含多訊號並發案例)
- 驗證方式:自動測試(fixture 比對 + 抑制規則斷言)
- 狀態:⬜

### T-06 planner
- 對應:F-05 / AC-09, AC-10
- 依賴:T-04 或 T-05 任一(吃統一 Signal)
- 產出:`core/planner.py`
- 驗證方式:自動測試(R:R 計算、rejected 分支)
- 狀態:⬜

### T-07 風控模組
- 對應:F-06 / AC-11, AC-12, AC-13
- 依賴:T-06
- 產出:`core/risk.py`
- 驗證方式:自動測試(部位公式、曝險上限、回撤熔斷三情境)
- 狀態:⬜

### T-08 模擬撮合引擎 + 台帳
- 對應:F-07 / AC-14, AC-15
- 依賴:T-07
- 產出:`core/broker.py` + `state/*.csv` 讀寫 + append-only 保護
- 驗證方式:自動測試(fixture 行情逐K推進比對)+ 盲審
- 狀態:⬜

### T-09 績效統計
- 對應:F-08 / AC-16
- 依賴:T-08
- 產出:`core/stats.py`(勝率/平均R/MDD/獲利因子,分軌)
- 驗證方式:自動測試(10 筆已知交易 fixture)
- 狀態:⬜

### T-10 報告頁
- 對應:F-09 / AC-18
- 依賴:T-09
- 產出:`report/build_report.py` → `docs/index.html`(手機優先版面)
- 驗證方式:盲審 + 人工檢核(390px)
- 狀態:⬜

### T-11 GitHub Actions 排程 + 狀態回寫
- 對應:F-09 / AC-17;ADR-003, ADR-004
- 依賴:T-02, T-03, T-08, T-10
- 產出:`.github/workflows/main.yml`(3 cron + bot commit + skip ci)
- 驗證方式:手動 dispatch 全管線跑通 + 盲審
- 狀態:⬜

## 開發順序建議

1. T-01(骨架,讓 tester 有東西可跑)
2. T-04 + T-02(籌碼軌先垂直打通:資料→訊號)
3. T-06 → T-07 → T-08 → T-09(共用下游,用籌碼軌 fixture 驗證)
4. T-03 + T-05(技術軌補上,直接接既有下游)
5. T-10 → T-11(報告與排程收尾)

## 完成定義(Definition of Done)

一個任務只有同時滿足以下才算 ✅:
1. tester 的對應 AC 測試全綠
2. reviewer 盲審通過(無 blocker 級意見)
3. `scripts/verify.sh` 全過(lint + test)
4. 主席在驗收卡打勾
