📋 回報單 #T-12：TAIFEX 日行情端點 CSV 後援解析
━━━━━━━━━━━━━━━━━━
分支：`t-12-taifex-csv-fallback`（自 main `3f00495` 切出）
Commit：`ae6a6ad`（派工單本身）、`62fcf3a`（實作）、本回報單另一個 commit
PR 開啟的 UTC 時間：見 PR 描述（回報單先於 PR 建立，無法預先寫入）

## 一、Diff 摘要

`git diff --stat main...`（實作 commit，不含派工單與本回報單）：

```
 adapters/taifex.py                      |  40 ++++++++-
 tests/fixtures/taifex_ohlc_20261014.csv |   7 ++
 tests/test_taifex.py                    | 153 ++++++++++++++++++++++++++++++--
 3 files changed, 191 insertions(+), 9 deletions(-)
```

`adapters/taifex.py`
- `_get_json(url)` → `_get_rows(url, csv_map, timeout=30)`：requests 呼叫（timeout、User-Agent、`raise_for_status()`）與原本相同。先 `resp.json()`，能解析就原樣回傳；拋 `ValueError`（含 `requests.exceptions.JSONDecodeError`）時：
  - `csv_map is None` → `RuntimeError(f"TAIFEX {url} 回應非 JSON")`；
  - 否則 `resp.content.decode("utf-8-sig")` → 標準庫 `csv.DictReader`，表頭與值皆 `.strip()`，依 `csv_map` 換成 JSON 鍵、只保留對照表內的欄、值保持字串；表頭缺任一鍵 → `ValueError("TAIFEX CSV 缺欄:" + 缺的欄名)`；成功才記 `log.warning("TAIFEX %s 回傳 CSV,改用 CSV 解析", url)`。
- 新增模組常數 `_OHLC_CSV_MAP`（照派工單原文）。
- `fetch_daily`：`parse_ohlc(_get_rows(OHLC_ENDPOINT, _OHLC_CSV_MAP))`、`parse_institutional(_get_rows(INST_ENDPOINT, None))`，其餘流程未動。
- 新增 import：`csv`、`io`（標準庫）。未新增第三方套件。
- 輔助函式 `_parse_csv_rows(text, csv_map)` 是把 CSV 解析從 `_get_rows` 抽出的私有函式（派工單未點名，屬實作細節）。

`tests/test_taifex.py`
- 既有 3 條測試只把 `monkeypatch` 目標由 `_get_json` 改為 `_get_rows`（因函式更名，必須改），assert 未放寬。
- 新增 10 條測試（含派工單指定 5 條，名稱完全相同）。

`tests/fixtures/taifex_ohlc_20261014.csv`：照派工單原文，第一行開頭有 UTF-8 BOM（`EF BB BF`，已用 `xxd` 確認；另有 `test_fixture_has_utf8_bom` 守住）。

未動：`config.py`、`scripts/`、`core/`、`signals/`、`.github/workflows/main.yml`、`data/`、`state/`，以及 `parse_ohlc`、`parse_institutional`、`append_row`、`HISTORY_COLUMNS`。

## 二、驗收清單

- [x] `test_get_rows_csv_fallback_ohlc`：`parse_ohlc(_get_rows(...))` == `{"date": "2026-10-14", "open": 48789.0, "high": 49200.0, "low": 48650.0, "close": 48792.0, "volume": 44607.0}`。PASSED
- [x] `test_fetch_daily_with_csv_ohlc_appends_row`：OHLC 回 CSV、INST 回 `INST_ROWS`（日期改 `20261014`）；tmp 歷史檔新列 OHLCV 同上，三個法人欄為 12345.0 / -678.0 / 90.0。PASSED
- [x] `test_inst_non_json_raises_and_writes_nothing`：先在 tmp 歷史檔寫入一列、記下 bytes，INST 回非 JSON → 拋 `RuntimeError`，檔案 bytes 前後完全相同。PASSED
- [x] `test_csv_missing_column_raises`：把表頭「交易時段」改名 → 拋 `ValueError`，訊息含「交易時段」。PASSED（另有 `test_csv_missing_column_writes_nothing`，確認 `fetch_daily` 路徑下歷史檔不被建立）
- [x] `test_json_path_unchanged`：`json()` 回 list → `_get_rows` 回傳物件 `is` 同一個（csv_map 為對照表或 None 兩種都測）。PASSED
- [x] 交叉驗證：`data/taifex_history.csv` 的 2026-10-14 列（10/15 run 走 JSON 路徑寫入）與 CSV fixture 解析結果完全相同。

  ```
  $ grep '^2026-10-14,' data/taifex_history.csv
  2026-10-14,48789.0,49200.0,48650.0,48792.0,44607.0,-86814.0,78417.0,2314.0
  ```
  open/high/low/close/volume = 48789 / 49200 / 48650 / 48792 / 44607，與 fixture 解析結果一致。
- [x] 既有測試全過：`scripts/verify.sh`（Python 3.14.5、venv 在 `~/.venvs/tide`，不在 repo 內）

  ```
  == lint(py_compile)==
  OK
  == pytest ==
  ..............................................................           [100%]
  62 passed in 0.42s
  == verify.sh 全過 ==
  ```
  `tests/test_taifex.py` 共 15 條全 PASSED。CI 用 Python 3.11；程式用到的語法（`X | None` 註解、`from __future__ import annotations`）在 3.11 皆可用，但本機未實際跑 3.11。
- [x] 實網煙霧（唯讀、不寫檔）。2026-10-19T05:44:28Z 執行：

  ```
  $ curl -sI -A tide-paper-trader https://openapi.taifex.com.tw/v1/DailyMarketReportFut
  HTTP/2 200
  content-type: application/octet-stream
  content-disposition: attachment; filename=DailyMarketReportFut.json
  cf-cache-status: HIT

  $ python -c "from adapters import taifex as t; print(t.parse_ohlc(t._get_rows(t.OHLC_ENDPOINT, t._OHLC_CSV_MAP)))"
  {'date': '2026-10-16', 'open': 47228.0, 'high': 47634.0, 'low': 47140.0, 'close': 47242.0, 'volume': 63312.0}
  ```
  當下端點回的是 **JSON**（`filename=DailyMarketReportFut.json`），所以此次煙霧測試走的是 JSON 路徑，**CSV 路徑沒有被實網驗證**，只由 fixture 與單元測試覆蓋。
  順帶一提：端點即使回 JSON 也帶 `content-disposition: attachment`，所以不能用這個標頭判斷格式，「先試 JSON、失敗再退 CSV」才是對的。
- [x] `git diff --stat` 只出現範圍內 3 個檔案（見上；`data`/`state`/`config.py`/`scripts`/`core`/`signals`/`.github` 的 diff 為 0 行）。

## 三、五席審查結論（commit-review-council，序列、單一 context）

審查範圍：3 檔，+191/-9。第一輪：**❌ FAIL（🔴 1 / 🟡 0 / 🔵 6）**。修正 🔴 後只複查該項：**✅ PASS（🔴 0 / 🟡 0 / 🔵 6，其中 1 項已順手套用）**。

🔴（已修正並複查）
1. `_get_rows`：我最初加了「JSON 可解析但非 list 就拋 `RuntimeError`」。派工單只寫「成功且是 list 就原樣回傳」，沒說非 list 怎麼辦；我的做法會讓端點回 `{}` 時從「無新資料、正常結束」變成 daily 失敗，等於改了派工單要求不變的 JSON 路徑。已移除，現在可解析的 JSON 一律原樣回傳（與舊行為相同）。複查：verify.sh 62 passed，程式碼確認 `return resp.json()`。

🟡：無。

🔵（未動，供參考）
1. [既有] `_iso_date` 遇到 `2026/10/14` 之類格式會產生 `2026-/1-0/`。CSV 帶來新的日期來源，但 INST 只吃 JSON，OHLC/INST 日期比對會擋下，不會寫入。
2. JSON 被截斷且 `csv_map` 非 None 時，錯誤訊息會是「CSV 缺欄」，略有誤導；仍是大聲失敗、不寫檔。
3. CSV 資料列欄位少於表頭時，值變 `""`，`parse_ohlc` 回 None 而不寫入（有 warning log），方向安全。
4. `taifex.py` 檔頭註解未提到 CSV 後援。
5. 既有測試的 monkeypatch lambda 寫 `csv_map=None` 預設，比真實簽名寬鬆。
6. `_FakeResp` 不是真的 `requests.Response`。**已套用**：另加 `test_real_response_with_bom_csv_is_not_json`，用真的 `Response` 驗證 BOM CSV 會讓 `json()` 拋 `ValueError` 並完整走完 CSV 解析。

各視角：邏輯＝1 🔴＋3 🔵 ｜ 安全＝無發現 ｜ 效能＝無發現 ｜ 風格＝2 🔵 ｜ 測試＝1 🔵（已套用）。

突變檢查（暫改程式碼後立刻還原，已 diff 確認還原一致），新測試全部抓得到：`utf-8-sig`→`utf-8`（4 條失敗）、拿掉值 strip（1 條）、拿掉表頭 strip（1 條）、`csv_map=None` 也走 CSV（1 條）、略過缺欄檢查（2 條）。

## 四、卡住的點與需要 Daniel／審 diff 者留意

- 沒有卡住，無需停下回報。
- 判斷點：派工單對「JSON 可解析但非 list」沒有規定。我選擇不特別處理（維持舊行為），理由見上方 🔴。若指揮官希望非 list 時明確失敗，請另開單。
- 錯誤訊息裡多個缺欄名我用「、」連接（派工單只寫 `+ 缺的欄名`，未指定分隔符）。
- 觀察（不在本單範圍，未處理）：`data/taifex_history.csv` 目前最後一列是 2026-10-15，而端點此刻已經是 2026-10-16 的資料。下一次 daily 應會補上；若 daily 又因 TAIFEX 失敗，請依派工單末段「給 Daniel」的補跑指令處理，避免該日 K 棒遺失。
- 本單沒有 merge、沒有觸發任何 workflow、沒有動 main。
━━━━━━━━━━━━━━━━━━
