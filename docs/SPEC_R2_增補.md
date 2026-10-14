# SPEC 第二輪增補(R2)

> 本檔與 `docs/第二輪規格修訂清單_2026-10-14.md`(規範性,含完整驗收條件)、
> `docs/R2_裁決紀錄_2026-10-14.md` 共同構成第二輪規格;第一輪 `SPEC.md` 保留原文不改,
> 與本檔衝突處以本檔為準。

## 對第一輪 AC 的修訂

| 原 AC | 修訂 |
|---|---|
| AC-11(部位公式) | 「進場價」明定為**實際成交價**(次K開盤±滑價);size 於成交時計定(R2-01)。停損出場實虧 = 資本×1%×(1+出場滑價/停損距離),上限 1.1%(R2-02 門檻下)。 |
| AC-13(熔斷) | 熔斷採 A 案狀態機:halted(冷卻 20 交易日、解除須無持倉、peak 重置)→ 第 3 次熔斷 terminated(R2-05、D-01)。既有持倉不受影響之語義不變。 |
| AC-14(撮合) | 出場價:stop 與 invalidated 加不利滑價;target 以目標價成交(R2-03)。持倉上限按週期:1d=20 根、1h=120 根(R2-04)。 |
| AC-15(台帳) | 第二輪台帳 `state/r2/ledger.csv` 採 23 欄擴充 schema(R2-07);第一輪封存檔 schema 不動。 |

## 新增約定

- **路徑**(R2-06):第二輪帳務與遙測一律在 `state/r2/`;第一輪三檔(`state/ledger.csv`、`state/equity.csv`、`state/positions.csv`)自 `round1-final` tag 起原地凍結,任何程式不得寫入。`state/cursor.csv` 跨輪延續;切換時第一輪 `state/pending_orders.csv` 應為空。
- **遙測**(R2-08):`state/r2/rejects.csv`(統一 reason 碼表,見 `core/broker.py` REASON_CODES)、`state/r2/halts.csv`(熔斷事件,恰一列/次)、`state/r2/signal_diag.csv`(訊號端門檻距離)皆 append-only;遙測寫入失敗不得中斷主管線;嚴禁從診斷數據期中反推調參。
- **預註冊**(R2-10/R2-12):`docs/R2_PREREG.md` 於第二輪首筆交易入帳前 commit 並凍結,視同 config 參數。
- **範圍紀律**(R2-11):本輪未做任何結果式剔除(含 USDJPY、tech/breakout);範圍變更僅允許機制理由。

## 紅線(不變)

零真實下單;第二輪統計歸零、永不宣稱「第二輪比第一輪進步」;零隨機;config+prereg 啟動後凍結;台帳 append-only;保守偏差。
