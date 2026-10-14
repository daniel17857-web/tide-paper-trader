# core/models.py — 統一資料結構(ADR-001:planner 之後所有模組吃同一套)
# 慣例:標的代號與日期一律字串(ISO 格式),禁止隱式型別轉換。
# R2 變更:Order 為「資格單」(size 於成交時定,R2-01);Position/Trade 擴充
# 量測欄位(R2-07);PortfolioState 增各軌熔斷狀態(R2-05 A 案)。
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class Signal:
    track: str        # "chip" | "tech"
    symbol: str       # 例 "MTX", "EURUSD"(字串)
    date: str         # 訊號K的時間戳,ISO 字串(日K "2026-07-23",小時K 含時分)
    kind: str         # chip: zscore/streak/resonance;tech: breakout/pullback/momentum
    direction: str    # "long" | "short"
    timeframe: str = "1d"   # "1d" | "1h"
    suppressed: bool = False  # AC-08:同日多訊號被抑制者標記,不進 planner
    meta: dict = field(default_factory=dict)


@dataclass
class Plan:
    signal: Signal
    entry_ref: float = 0.0    # 進場參考價(訊號K收盤;實際成交=次K開盤±滑價)
    entry_zone: tuple = (0.0, 0.0)  # (低, 高) 進場區間
    stop: float = 0.0
    target: float = 0.0
    invalidation: str = ""    # 失效條件(文字描述,broker 依此判定)
    rr: float = 0.0           # 計畫 RR(以 entry_ref 計;成交時另有 fill_rr 複檢)
    status: str = "ok"        # "ok" | "rejected"
    reject_reason: str = ""   # 統一碼表見 core/broker.py REASON_CODES

    def to_row(self) -> dict:
        d = asdict(self)
        sig = d.pop("signal")
        d.update({f"signal_{k}": v for k, v in sig.items() if k != "meta"})
        d["entry_zone"] = f"{self.entry_zone[0]}~{self.entry_zone[1]}"
        return d


@dataclass
class Order:
    plan: Plan
    size: float = 0.0         # R2-01:風控階段僅為「預估」(entry_ref 基準);
                              # 實際 size 於成交時以實際進場價重算
    status: str = "approved"  # "approved" | "rejected"
    reject_reason: str = ""   # 統一碼表見 core/broker.py REASON_CODES


@dataclass
class Position:
    track: str
    symbol: str
    timeframe: str
    kind: str
    direction: str            # "long" | "short"
    size: float
    entry_date: str
    entry_price: float        # 實際成交價(次K開盤±滑價)
    stop: float
    target: float
    bars_held: int = 0
    # ── R2-07 進場時量測欄位(平倉建 Trade 時帶出)──
    planned_entry_ref: float = 0.0
    planned_rr: float = 0.0
    fill_rr: float = 0.0          # 成交時以實際進場價複算的 RR
    risk_nominal: float = 0.0     # 名目單筆風險(= 資本×RISK_PCT)
    risk_actual: float = 0.0      # 實際單筆風險(size×每點價值×|entry−stop|,帳戶幣別)
    slippage_cost_entry: float = 0.0  # 進場滑價成本(帳戶幣別)

    @property
    def dir_mult(self) -> int:
        return 1 if self.direction == "long" else -1


@dataclass
class Trade:
    track: str
    symbol: str
    timeframe: str
    kind: str                 # 訊號類型
    direction: str
    size: float
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float         # 含出場滑價之實際出場價(R2-03)
    exit_reason: str          # "stop" | "target" | "invalidated"
    r_multiple: float
    pnl: float                # 軌別本位幣(chip=TWD, tech=USD)
    # ── R2-07 擴充欄位 ──
    stop: float = 0.0
    target: float = 0.0
    planned_entry_ref: float = 0.0
    planned_rr: float = 0.0
    fill_rr: float = 0.0
    risk_nominal: float = 0.0
    risk_actual: float = 0.0
    slippage_cost_entry: float = 0.0
    slippage_cost_exit: float = 0.0
    round: int = 2

    def to_row(self) -> dict:
        return asdict(self)


# 第二輪台帳 schema(R2-07);第一輪封存檔維持原 13 欄不動
LEDGER_COLUMNS = [
    "track", "symbol", "timeframe", "kind", "direction", "size",
    "entry_date", "entry_price", "exit_date", "exit_price",
    "exit_reason", "r_multiple", "pnl",
    "stop", "target", "planned_entry_ref", "planned_rr", "fill_rr",
    "risk_nominal", "risk_actual", "slippage_cost_entry", "slippage_cost_exit",
    "round",
]


# 各軌熔斷狀態(R2-05 A 案)
TRACK_ACTIVE = "active"
TRACK_HALTED = "halted"
TRACK_TERMINATED = "terminated"


@dataclass
class TrackStatus:
    status: str = TRACK_ACTIVE     # active | halted | terminated
    halt_date: str = ""            # 最近一次熔斷日(K棒日,ISO 字串)
    resets: int = 0                # 已重置次數(≤ HALT_MAX_RESETS)


@dataclass
class PortfolioState:
    positions: list = field(default_factory=list)   # list[Position]
    equity: dict = field(default_factory=dict)      # track -> float
    peak_equity: dict = field(default_factory=dict) # track -> float
    track_status: dict = field(default_factory=dict)  # track -> TrackStatus

    def open_count(self, track: str | None = None) -> int:
        if track is None:
            return len(self.positions)
        return sum(1 for p in self.positions if p.track == track)

    def drawdown(self, track: str) -> float:
        peak = self.peak_equity.get(track, 0.0)
        if peak <= 0:
            return 0.0
        return (peak - self.equity.get(track, peak)) / peak

    def status_of(self, track: str) -> TrackStatus:
        if track not in self.track_status:
            self.track_status[track] = TrackStatus()
        return self.track_status[track]
