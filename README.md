# 潮汐模擬倉(tide-paper-trader)

雙軌**模擬**交易管線:台指期籌碼訊號 vs 外匯技術訊號,GitHub Actions 每日自動執行、
GitHub Pages 出報告,跑 8 週產出雙軌績效對照。**全程零真實資金,本 repo 不含任何真實下單程式碼。**

規格與架構見 `docs/SPEC.md`、`docs/ARCH.md`、`docs/TASKS.md`。

## 本機執行

```bash
pip install -r requirements.txt
python scripts/run_pipeline.py --mode daily     # 台北 16:30 主跑(台指期+外匯日K)
python scripts/run_pipeline.py --mode london    # 倫敦開盤加掃(小時K)
python scripts/run_pipeline.py --mode newyork   # 紐約開盤加掃(小時K)
scripts/verify.sh                               # lint + 全測試
```

## 部署(GitHub)

1. 推上 GitHub 後,Settings → Pages → Source 選 `main` 分支 `/docs` 目錄。
2. Actions 需要 `contents: write`(workflow 已宣告),三個 cron 自動跑,
   也可在 Actions 頁手動 dispatch 指定模式。
3. 狀態(`data/`、`state/`、`docs/index.html`)由 bot commit 回寫,附 `[skip ci]` 防迴圈。

## 目錄

| 路徑 | 內容 |
|------|------|
| `adapters/` | TAIFEX Open API、yfinance 外匯抓取 |
| `signals/` | 籌碼軌(zscore/連續買賣超/共振)、技術軌(突破/回調/動能)|
| `core/` | models / planner / risk / broker(模擬撮合)/ stats |
| `state/` | positions / equity / ledger(append-only 台帳)/ pending / cursor |
| `report/` | 靜態 HTML 報告產生器(手機優先)|

## 固定假設(評估期內凍結,詳見 `config.py`)

- 小台每點 50 元;外匯 10 萬名目為 1 口基準,不計隔夜利息
- 進場 = 訊號次一根K開盤 ± 固定滑價(台指期 2 點、外匯 2 pips)
- 同K同時觸及停損與目標 → 以停損計(保守偏差)
- 模擬允許小數口數,使單筆風險精確 = 虛擬資金 1%

> 模擬 ≠ 實盤:結果只用於雙軌相對比較,不代表實盤絕對報酬,不構成投資建議。
