# ARCH.md — 潮汐模擬倉(tide-paper-trader)

> **一句話結論**:純 Python 批次管線 + CSV/DuckDB 儲存 + GitHub Actions 排程 + GitHub Pages 報告,零伺服器、零月費,與既有 ETF 管線同構,雙軌差異全部收斂在 adapter 層。

## 1. 技術棧總覽

| 層 | 選型 | 備註 |
|----|------|------|
| 語言 | Python 3.11+ | 與既有管線一致 |
| 資料抓取 | requests(TAIFEX)、yfinance(外匯) | adapter 各自獨立 |
| 儲存 | CSV(累積歷史)+ DuckDB(分析查詢) | 沿用既有慣例 |
| 計算 | pandas + DuckDB SQL | 訊號雙實作不需要,本案只用 pandas |
| 測試 | pytest + fixture 比對 | tester 的工具 |
| 排程 | GitHub Actions(cron, UTC) | 雲端執行,免本機 |
| 報告 | 靜態 HTML → GitHub Pages | 手機可看,已定案方案 |

## 2. 架構決策紀錄(ADR)

### ADR-001 單一管線、雙 adapter,而非兩個獨立專案
- **狀態**:已採納
- **背景**:台指期與外匯的資料源、訊號邏輯不同,但計畫/風控/撮合/統計完全共用。
- **決策**:掃描層與訊號層各留 interface,雙軌各自實作 adapter;planner 之後的所有模組吃統一的 `Signal` / `Plan` / `Trade` 資料結構。
- **理由**:對照實驗要公平,唯一方法是下游完全同一套程式;也省一半維護。
- **放棄的替代方案**:兩個 repo 各跑各的——對照時指標定義容易漂移,放棄。
- **後果**:資料結構要先定好,前期多半天設計成本。

### ADR-002 模擬撮合用「次K開盤價 + 固定滑價」,不做盤中模擬
- **狀態**:已採納
- **背景**:日K訊號在收盤後才確立,真實世界最快也是下一根K才能進場。
- **決策**:進場 = 訊號次一根K開盤價 ± 滑價(台指期 2 點、外匯 2 pips);停損/目標以後續K的高低點觸價判定;同K雙觸發以停損計(保守偏差)。
- **理由**:規則簡單、可重現、偏保守——模擬績效寧可低估不可高估。
- **放棄的替代方案**:小時K內插模擬盤中路徑——複雜度高且仍是猜,放棄。
- **後果**:實際可得成交價可能比模擬更好,對照結論偏保守可接受。

### ADR-003 排程用 GitHub Actions,接受 cron 分鐘級延遲與 DST 偏移
- **狀態**:已採納
- **背景**:需要每日 3 個時點自動執行,Daniel 已傾向雲端排程。
- **決策**:三個 workflow cron(UTC):08:30(台指期盤後主跑)、08:00(倫敦加掃)、13:30(紐約加掃)。
- **理由**:免費、免本機開機、與 repo 同生命週期。
- **放棄的替代方案**:本機 cron / 雲端 VM——維運成本高,放棄。
- **後果**:Actions cron 可能延遲數分鐘;夏令時間偏移 1 小時(A-05 已接受)。

### ADR-004 狀態(持倉、權益)存 repo 內 CSV,由 Actions commit 回寫
- **狀態**:已採納
- **背景**:模擬倉需要跨執行保存持倉與權益曲線,但不想引入資料庫服務。
- **決策**:`state/positions.csv`、`state/equity.csv`、`state/ledger.csv` 存在 repo,每次排程執行後由 workflow 自動 commit。
- **理由**:零依賴、天然有版本紀錄(每天的狀態變化都是一個 commit,可回溯稽核)。
- **放棄的替代方案**:Supabase——本案規模用不到,留作未來實盤階段選項。
- **後果**:repo 會有機器 commit;需在 workflow 設定 bot 身分與防迴圈(`[skip ci]`)。

## 3. 模組切分與介面

```
tide-paper-trader/
├── adapters/
│   ├── taifex.py        # fetch_daily() -> pd.DataFrame
│   └── forex.py         # fetch_daily(), fetch_hourly() -> pd.DataFrame
├── signals/
│   ├── chip.py          # 籌碼軌:detect(df) -> list[Signal]
│   └── technical.py     # 技術軌:detect(df) -> list[Signal]
├── core/
│   ├── models.py        # Signal / Plan / Order / Trade dataclass
│   ├── planner.py       # make_plan(signal, df) -> Plan | Rejected
│   ├── risk.py          # check(plan, state) -> Approved | Rejected(reason)
│   ├── broker.py        # 模擬撮合:on_new_bar(state, bars) -> state'
│   └── stats.py         # summarize(ledger) -> per-track metrics
├── report/
│   └── build_report.py  # state + ledger -> docs/index.html(Pages)
├── state/               # positions.csv / equity.csv / ledger.csv
├── tests/               # pytest + fixtures/
└── .github/workflows/   # main.yml(3 cron)
```

- 關鍵介面(只有簽名):
  - `Signal(track, symbol, date, kind, direction, meta)`
  - `Plan(signal, entry_zone, stop, target, invalidation, rr)`
  - `risk.check(plan: Plan, state: PortfolioState) -> Decision`
  - `broker.on_new_bar(state: PortfolioState, bars: dict[str, Bar]) -> PortfolioState`

## 4. builder 必守約束

- 標的代號、日期一律字串/ISO 格式讀入,禁止隱式型別轉換(沿用 ETF 管線慣例)。
- 訊號與撮合不得有任何隨機性;所有參數集中在 `config.py`,8 週評估期內凍結。
- 不引入 SPEC/ARCH 未列的第三方套件;需要時先在 PR 說明提出。
- 任何觸及「真實下單」的程式碼一律不寫,包括註解掉的樣板。
- 台帳為 append-only,禁止就地修改歷史列。
