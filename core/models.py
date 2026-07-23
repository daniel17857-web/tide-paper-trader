# core/models.py — 統一資料結構(ADR-001:planner 之後所有模組吃同一套)
# 慣例:標的代號與日期一律字串(ISO 格式),禁止隱式型別轉換。
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
    rr: float = 0.0
    status: str = "ok"        # "ok" | "rejected"
    reject_reason: str = ""   # 例 "rr_below_min"

    def to_row(self) -> dict:
        d = asdict(self)
        sig = d.pop("signal")
        d.update({f"signal_{k}": v for k, v in sig.items() if k != "meta"})
        d["entry_zone"] = f"{self.entry_zone[0]}~{self.entry_zone[1]}"
        return d


@dataclass
class Order:
    plan: Plan
    size: float = 0.0         # 口數/名目單位(模擬允許小數,使單筆風險精確=1%)
    status: str = "approved"  # "approved" | "rejected"
    reject_reason: str = ""   # exposure_cap / drawdown_halt / ...


@dataclass
class Position:
    track: str
    symbol: str
    timeframe: str
    kind: str
    direction: str            # "long" | "short"
    size: float
    entry_date: str
    entry_price: float
    stop: float
    target: float
    bars_held: int = 0

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
    exit_price: float
    exit_reason: str          # "stop" | "target" | "invalidated"
    r_multiple: float
    pnl: float                # 軌別本位幣(chip=TWD, tech=USD)

    def to_row(self) -> dict:
        return asdict(self)


LEDGER_COLUMNS = [
    "track", "symbol", "timeframe", "kind", "direction", "size",
    "entry_date", "entry_price", "exit_date", "exit_price",
    "exit_reason", "r_multiple", "pnl",
]


@dataclass
class PortfolioState:
    positions: list = field(default_factory=list)   # list[Position]
    equity: dict = field(default_factory=dict)      # track -> float
    peak_equity: dict = field(default_factory=dict) # track -> float

    def open_count(self, track: str | None = None) -> int:
        if track is None:
            return len(self.positions)
        return sum(1 for p in self.positions if p.track == track)

    def drawdown(self, track: str) -> float:
        peak = self.peak_equity.get(track, 0.0)
        if peak <= 0:
            return 0.0
        return (peak - self.equity.get(track, peak)) / peak
