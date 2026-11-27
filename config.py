# config.py — 所有參數集中於此,8 週評估期內凍結(交接摘要紅線 3)
# 任何模組不得自帶魔術數字;修改參數視同重啟評估期。

# ── 虛擬資金(每軌獨立計價,對照以 R 倍數與比率型指標為準,不跨幣別加總)──
CAPITAL = {
    "chip": 1_000_000.0,   # 籌碼軌:新台幣(小台指)
    "tech": 30_000.0,      # 技術軌:美元(外匯)
}
RISK_PCT = 0.01            # 單筆風險 = 虛擬資金 1%(AC-11)

# ── 曝險與熔斷(F-06;R2-05 採 A 案:冷卻+重置+次數上限)──
MAX_OPEN_TOTAL = 3         # 同時持倉 ≤ 3 筆(全帳戶)
MAX_OPEN_PER_TRACK = 2     # 同軌 ≤ 2 筆
DRAWDOWN_HALT = 0.10       # 權益自高點回落 ≥ 10% 停止新倉(各軌獨立計算;prereg 禁止放寬)
HALT_COOLDOWN_DAYS = 20    # 熔斷冷卻:該軌自身K棒日曆 20 個交易日,且解除時須無持倉(D-01)
HALT_MAX_RESETS = 2        # 重置上限:同軌第 3 次熔斷 → 本輪終止取樣(track_terminated)

# ── 商品規格 ──
MTX_SYMBOL = "MTX"         # 小台指(模擬計價用代號,字串)
MTX_POINT_VALUE = 50.0     # 小台每點 50 元(A-04)
FX_PAIRS = ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD"]
FX_YF_TICKERS = {p: p + "=X" for p in FX_PAIRS}

# ── 滑價(ADR-002:進場 = 次K開盤 ± 滑價)──
SLIPPAGE_MTX_POINTS = 2.0  # 台指期 2 點
SLIPPAGE_FX_PIPS = 2.0     # 外匯 2 pips(JPY 對 pip=0.01,其餘 0.0001)


def pip_size(symbol: str) -> float:
    return 0.01 if "JPY" in symbol else 0.0001


def slippage(track: str, symbol: str) -> float:
    if track == "chip":
        return SLIPPAGE_MTX_POINTS
    return SLIPPAGE_FX_PIPS * pip_size(symbol)


# ── 籌碼軌訊號(F-03)──
ZSCORE_WINDOW = 20         # z-score 基線視窗(不含當日)
ZSCORE_THRESHOLD = 2.0     # |z| ≥ 2σ 觸發
STREAK_DAYS = 5            # 連續買/賣超 ≥ 5 日
CHIP_PRIORITY = ["resonance", "zscore", "streak"]  # 同日多訊號抑制優先序

# ── 技術軌訊號(F-04)──
BREAKOUT_WINDOW = 20       # 收盤突破前 20 根高/低點(不含當根)
EMA_WINDOW = 20
EMA_TREND_LOOKBACK = 5     # EMA20 較 5 根前上升/下降 → 趨勢方向
PULLBACK_TOLERANCE = 0.001 # 觸及 20EMA「附近」= EMA ± 0.1%
ATR_WINDOW = 14
MOMENTUM_ATR_MULT = 1.5    # 單根實體 ≥ 1.5×ATR14
TECH_PRIORITY = ["breakout", "momentum", "pullback"]  # AC-08 固定優先序

# ── planner(F-05)──
STRUCTURE_WINDOW = 10      # 結構低/高點 = 近 10 根極值(含訊號當根)
STOP_ATR_MULT = 1.5        # 停損候選:1.5×ATR14,與結構極值取「較近者」
TARGET_R = 2.0             # 目標候選:2R,與前波高/低取「較近者」
PRIOR_EXTREME_WINDOW = 20  # 前波高/低 = 前 20 根極值(不含當根)
MIN_RR = 1.5               # R:R < 1.5 → rejected(AC-10);R2-01 於成交時以實際進場價複檢

# ── 撮合(F-07;R2-02/03/04)──
MIN_STOP_SLIP_RATIO = 10.0  # 停損距離須 ≥ 10×單邊滑價(R2-02,planner 預檢+成交複檢)
MAX_HOLD_BARS = {"1d": 20, "1h": 120}  # 持倉上限按週期(R2-04);缺鍵即 KeyError,禁止默認值

# ── 路徑(R2-06:第二輪帳務一律在 state/r2/,第一輪三檔原地凍結;cursor 跨輪延續)──
DATA_DIR = "data"
STATE_DIR = "state"            # 第一輪封存檔與跨輪 cursor 所在
R2_STATE_DIR = "state/r2"      # 第二輪台帳/權益/持倉/pending/遙測
REPORT_PATH = "docs/index.html"
ROUND = 2                      # 台帳 round 欄位值
