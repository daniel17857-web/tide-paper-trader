📋 派工單 #T-12：TAIFEX 日行情端點 CSV 後援解析
━━━━━━━━━━━━━━━━━━
【目標】
OpenAPI `/v1/DailyMarketReportFut` 有時會經 CDN 回傳 CSV，而不是 JSON。2026-10-15 09:01Z 到至少 10:45Z 之間就是這樣，實測標頭為 `content-disposition: attachment; filename=DailyMarketReportFut.csv`、`cf-cache-status: HIT`。到 2026-10-19 05:39Z 已恢復回傳 JSON。

`adapters/taifex.py` 的 `_get_json()` 直接呼叫 `resp.json()`，遇到 CSV 會拋 JSONDecodeError，整個 daily run 失敗。第一輪 #21（7/31）、#39（8/10）兩次失敗都是這個原因。10/15 那次只是剛好 daily 在 13:36Z 才跑到，快取已經換回 JSON，才沒有出事。

`fetch_daily` 只抓最新一筆、不回補，所以 daily 失敗的那天，等 OpenAPI 換成下一天的資料，該日K就永久遺失。第一輪最後一筆 MTX 多單還在場上：`bars_held` 15/20，10/15 最低價 46,883，離停損 46,836.5 只差 46.5 點。剩下的K棒一根都不能漏。

本單讓日行情端點同時吃 JSON 與 CSV。法人端點維持只吃 JSON：它目前沒有 CSV 表頭樣本，不能用猜的。

【範圍】
- 動：`adapters/taifex.py`、`tests/test_taifex.py`、新增 `tests/fixtures/taifex_ohlc_20261014.csv`
- 不要動：其他所有檔案，包括 `config.py`、`scripts/`、`core/`、`signals/`、`.github/workflows/main.yml`、`data/`、`state/`。`parse_ohlc` 的選約邏輯（TX、一般時段、6 碼月份、排序取最近月）、`parse_institutional`、`append_row`、`HISTORY_COLUMNS` 一律不改。

【風險】高（不可逆資料寫入：`data/taifex_history.csv` 是 append-only 歷史檔，解析錯誤會永久寫入錯值）→ 五席審查

【細節方向】（已定案，照做）
1. 把 `_get_json(url)` 改成 `_get_rows(url: str, csv_map: dict | None, timeout: int = 30) -> list[dict]`：
   - requests 呼叫與現行相同：`timeout`、`headers={"User-Agent": "tide-paper-trader"}`、`raise_for_status()`。
   - 先 `resp.json()`。成功且是 list，就原樣回傳。
   - 拋 `ValueError`（`requests.exceptions.JSONDecodeError` 是它的子類）時：
     - `csv_map is None` → 拋 `RuntimeError(f"TAIFEX {url} 回應非 JSON")`，不寫任何檔。
     - 否則 `text = resp.content.decode("utf-8-sig")`，用標準庫 `csv.DictReader(io.StringIO(text))` 讀取。表頭與值都 `.strip()`。依 `csv_map` 把中文欄名換成 JSON 鍵，只保留 `csv_map` 裡的欄，值保持字串。
     - 如果表頭缺 `csv_map` 的任一個鍵，拋 `ValueError("TAIFEX CSV 缺欄:" + 缺的欄名)`，不寫任何檔。
     - 成功時記一行 `log.warning("TAIFEX %s 回傳 CSV,改用 CSV 解析", url)`。
2. 新增模組常數（這是格式對照，不是參數，不放 `config.py`）：
   ```python
   _OHLC_CSV_MAP = {
       "日期": "Date", "契約代號": "Contract", "到期月份(週別)": "ContractMonth(Week)",
       "開盤價": "Open", "最高價": "High", "最低價": "Low", "最後成交價": "Last",
       "合計成交量": "Volume", "交易時段": "TradingSession",
   }
   ```
3. `fetch_daily` 改成：`parse_ohlc(_get_rows(OHLC_ENDPOINT, _OHLC_CSV_MAP))`、`parse_institutional(_get_rows(INST_ENDPOINT, None))`。其餘流程一字不改。
4. 不加重試、不加新參數、不加新套件（`csv`、`io` 是標準庫）。
5. fixture `tests/fixtures/taifex_ohlc_20261014.csv` 用以下內容，取自 10:45Z 實抓的原文。第一行開頭要有 UTF-8 BOM（`﻿`）：
   ```
   日期,契約代號,到期月份(週別),開盤價,最高價,最低價,最後成交價,漲跌價,漲跌%,合計成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,是否因訊息面暫停交易,交易時段,價差對單式委託成交量
   20261014,TX,202610,48789,49200,48650,48792,400,0.83%,44607,48798,105601,48789,48794,50265,39852,,一般,
   20261014,TX,202610,48433,48873,48357,48755,363,0.75%,21801,NULL,-,48743,48755,50265,39852,,盤後,
   20261014,TX,202611,48899,49328,48799,48964,357,0.73%,2969,48962,8959,48947,48957,50440,44435,,一般,
   20261014,TX,202612,49125,49425,49011,49110,308,0.63%,54,49094,1160,49088,49100,50560,27382,,一般,
   20261014,TX,202610/202611,160,165,119,155,-,-,1179,-,-,158,163,252,119,,一般,1524
   20261014,TX,202709,-,-,-,-,-,-,0,NULL,-,50283,50365,51527,47604,,盤後,
   ```

【驗收標準】
□ `test_get_rows_csv_fallback_ohlc`：mock 的 response，`json()` 拋 `requests.exceptions.JSONDecodeError`，`content` 為上述 fixture 的 bytes。`parse_ohlc(_get_rows(OHLC_ENDPOINT, _OHLC_CSV_MAP))` 要等於 `{"date": "2026-10-14", "open": 48789.0, "high": 49200.0, "low": 48650.0, "close": 48792.0, "volume": 44607.0}`。
□ `test_fetch_daily_with_csv_ohlc_appends_row`：OHLC 回 CSV fixture，INST 回現有 `INST_ROWS`，日期改成 `20261014`。寫入 tmp 歷史檔的新列，OHLCV 與上一條相同，三個法人欄與 INST 值相同。
□ `test_inst_non_json_raises_and_writes_nothing`：INST 回非 JSON → `fetch_daily` 拋 `RuntimeError`，tmp 歷史檔的 bytes 與呼叫前完全相同。
□ `test_csv_missing_column_raises`：把 fixture 的「交易時段」欄名改掉 → 拋 `ValueError`，訊息含「交易時段」。
□ `test_json_path_unchanged`：`json()` 正常回 list 時，`_get_rows` 回傳的物件與 `json()` 回傳值相同（`is` 或 `==`）。
□ 交叉驗證：`data/taifex_history.csv` 的 2026-10-14 那列，是 10/15 的 run 走 JSON 路徑寫入的。它的 open/high/low/close/volume 必須與 CSV fixture 解析結果完全相同：48789、49200、48650、48792、44607。附 `grep '^2026-10-14,' data/taifex_history.csv` 的輸出。
□ 既有測試全過：`scripts/verify.sh` 輸出附上。
□ 實網煙霧（唯讀，不寫檔）：在 repo 根目錄執行 `python -c "from adapters import taifex as t; print(t.parse_ohlc(t._get_rows(t.OHLC_ENDPOINT, t._OHLC_CSV_MAP)))"`，輸出非 None，日期為最近一個已公布的交易日。附上輸出，並註明當下端點回的是 JSON 還是 CSV（用 `curl -sI` 看 `content-disposition`）。
□ `git diff --stat` 只出現範圍內的 3 個檔案。

【不可踩的線】
- 不寫任何真實下單程式碼。
- 日期、代號一律字串，不做隱式型別轉換。
- 歷史檔 append-only：不改既有列，失敗時一個 byte 都不寫。
- 不新增第三方套件，不改 `config.py`。
- 不自行 merge、不自行觸發 workflow。開 PR 後停下，等 Daniel 決定是否在第一輪尾聲合併（裁決 D-S2）。

【回報格式】
PR 連結 + diff + 驗收清單勾選結果 + 卡住的點。另外附 PR 開好時的 UTC 時間。

【給 Daniel：daily 失敗時的補救】
不論本單合併與否，只要 daily 因 TAIFEX 失敗，就要在 OpenAPI 換成下一天的資料之前（上次觀測約 22:36Z，即台北隔天 06:36）手動補跑一次：
`gh workflow run main.yml -R daniel17857-web/tide-paper-trader -f mode=daily`
補跑後確認 log 有「TAIFEX 新增 <該日日期>」。
━━━━━━━━━━━━━━━━━━
建議執行模型：Sonnet — 單一 adapter、有實抓 fixture 與明確期望值，不需要設計判斷
