# T-08(R2 改版):AC-14(逐K人工驗算)、AC-15(台帳欄位完整)
# + R2-01 成交時定部位、R2-02 成交複檢、R2-03 出場滑價、append-only、可重現性
import copy

import pytest

import config
from core import broker
from core.models import LEDGER_COLUMNS, Order, Plan, PortfolioState, Signal


def _order(track="tech", symbol="EURUSD", direction="long",
           entry=1.1000, stop=1.0950, target=1.1150, size=0.0):
    s = Signal(track=track, symbol=symbol, date="2026-06-01", kind="breakout",
               direction=direction)
    plan = Plan(signal=s, entry_ref=entry, stop=stop, target=target, rr=3.0)
    return Order(plan=plan, size=size, status="approved")


def _bar(ts, o, h, l, c):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c}


def _state():
    return PortfolioState(equity=dict(config.CAPITAL), peak_equity=dict(config.CAPITAL))


KEY = ("EURUSD", "1d")


def test_ac14_entry_and_target_exit_manual_case():
    """人工驗算(R2-01 成交時定部位):
    開盤 1.1010 + 滑價 0.0002 → 進場 1.1012;停損距離 0.0062
    size = 300 / 0.0062 = 48387.0968;fill RR = 0.0138/0.0062 = 2.2258 ≥ 1.5
    次K高 1.1160 觸目標 1.1150(target 不加滑價)
    R = 0.0138/0.0062 = 2.2258,pnl = 48387.0968 × 0.0138 = 667.74 USD。"""
    state = _state()
    state, closed, rej = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
    assert closed == [] and rej == [] and len(state.positions) == 1
    pos = state.positions[0]
    assert pos.entry_price == pytest.approx(1.1012)
    assert pos.size == pytest.approx(300.0 / 0.0062)
    assert pos.risk_actual == pytest.approx(300.0, abs=0.01)   # 恰為資本 1%
    assert pos.fill_rr == pytest.approx(2.2258, abs=0.0001)

    state, closed, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1045, 1.1160, 1.1030, 1.1120)})
    assert len(closed) == 1 and state.positions == []
    t = closed[0]
    assert t.exit_reason == "target"
    assert t.exit_price == pytest.approx(1.1150)
    assert t.r_multiple == pytest.approx(2.2258, abs=0.0001)
    assert t.pnl == pytest.approx(667.74, abs=0.01)
    assert state.equity["tech"] == pytest.approx(config.CAPITAL["tech"] + t.pnl, abs=0.01)


def test_r2_01_sizing_no_lookahead():
    """同開盤、不同高低收的兩根K → size 必須完全相同(無前視)。"""
    sizes = []
    for bar in (_bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040),
                _bar("2026-06-02", 1.1010, 1.1300, 1.0960, 1.0995)):
        state = _state()
        state, closed, _ = broker.on_new_bar(state, {KEY: bar}, [_order()])
        # 第二根K同根觸目標即平倉:size 從持倉或已平倉取皆可
        sizes.append(state.positions[0].size if state.positions else closed[0].size)
    assert sizes[0] == sizes[1]


def test_r2_01_rr_degraded_at_fill():
    """計畫 RR=2.0 通過,但開盤朝目標方向跳空使 fill RR < 1.5 → 不進場。"""
    order = _order(target=1.1100)  # 計畫 RR = 0.01/0.005 = 2.0
    state = _state()
    # 開盤 1.1010 → 進場 1.1012,fill RR = 0.0088/0.0062 = 1.419 < 1.5
    state, _, rej = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [order])
    assert state.positions == []
    assert rej[0]["reason"] == "rr_degraded_at_fill"


def test_r2_02_stop_too_tight_at_fill():
    """開盤朝停損方向跳空使停損距離 < 10×滑價 → 不進場。"""
    # stop 1.0950;開盤 1.0966 → 進場 1.0968,距離 0.0018 < 0.002
    order = _order(target=1.1150)
    state = _state()
    state, _, rej = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.0966, 1.1050, 1.0960, 1.1040)}, [order])
    assert state.positions == []
    assert rej[0]["reason"] == "stop_too_tight_at_fill"


def test_r2_03_stop_exit_with_slippage():
    """停損出場價 = stop − 滑價;r = −(1+滑價/停損距離)。"""
    state = _state()
    state, _, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1015, 1.1005, 1.1010)}, [_order()])
    state, closed, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1005, 1.1008, 1.0940, 1.0960)})
    t = closed[0]
    assert t.exit_reason == "stop"
    assert t.exit_price == pytest.approx(1.0950 - 0.0002)
    assert t.r_multiple == pytest.approx(-(1 + 0.0002 / 0.0062), abs=0.0001)
    # 實際入帳虧損 = 資本×1%×(1+出場滑價/停損距離)
    assert -t.pnl == pytest.approx(300.0 * (1 + 0.0002 / 0.0062), rel=0.01)


def test_same_bar_double_trigger_counts_as_stop():
    state = _state()
    state, _, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1020, 1.1000, 1.1010)}, [_order()])
    # 同K同時觸停損與目標 → 以停損計(紅線 5)
    state, closed, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1010, 1.1200, 1.0900, 1.1150)})
    assert closed[0].exit_reason == "stop"
    assert closed[0].exit_price == pytest.approx(1.0948)


def test_gap_through_stop_no_fill():
    state = _state()
    state, closed, rej = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.0940, 1.0960, 1.0930, 1.0950)}, [_order()])
    assert state.positions == [] and closed == []
    assert rej[0]["reason"] == "gap_through_stop"


def test_max_hold_expiry_exits_at_close_with_slippage():
    state = _state()
    state, _, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1015, 1.1005, 1.1010)}, [_order()])
    closed = []
    for i in range(config.MAX_HOLD_BARS["1d"] + 1):
        state, c, _ = broker.on_new_bar(
            state, {KEY: _bar(f"2026-07-{i + 1:02d}", 1.1010, 1.1015, 1.1005, 1.1013)})
        closed.extend(c)
    assert len(closed) == 1
    assert closed[0].exit_reason == "invalidated"
    assert closed[0].exit_price == pytest.approx(1.1013 - 0.0002)  # R2-03 出場滑價


def test_r2_04_hourly_hold_limit_120():
    """1h 持倉上限 120 根(R2-04)。"""
    s = Signal(track="tech", symbol="EURUSD", date="2026-06-01T00:00:00",
               kind="breakout", direction="long", timeframe="1h")
    plan = Plan(signal=s, entry_ref=1.1000, stop=1.0950, target=1.1150, rr=3.0)
    order = Order(plan=plan, size=0.0, status="approved")
    key = ("EURUSD", "1h")
    state = _state()
    state, _, _ = broker.on_new_bar(
        state, {key: _bar("2026-06-01T01:00:00", 1.1010, 1.1015, 1.1005, 1.1010)}, [order])
    closed = []
    bars_fed = 0
    for i in range(150):
        state, c, _ = broker.on_new_bar(
            state, {key: _bar(f"2026-06-02T{i:04d}", 1.1010, 1.1015,
                              1.1005, 1.1013)})
        closed.extend(c)
        bars_fed = i + 1
        if c:
            break
    assert len(closed) == 1
    assert closed[0].exit_reason == "invalidated"
    assert bars_fed == config.MAX_HOLD_BARS["1h"]  # 第 120 根才出場,1d 的 20 根不適用


def test_chip_pnl_in_twd_sized_at_fill():
    state = _state()
    order = _order(track="chip", symbol="MTX", entry=20000.0, stop=19900.0,
                   target=20200.0)
    key = ("MTX", "1d")
    state, _, _ = broker.on_new_bar(
        state, {key: _bar("2026-06-02", 20010.0, 20050.0, 19960.0, 20030.0)}, [order])
    pos = state.positions[0]
    assert pos.entry_price == pytest.approx(20012.0)        # +2 點滑價
    assert pos.size == pytest.approx(10000.0 / (50.0 * 112.0))  # 成交時定部位
    assert pos.risk_actual == pytest.approx(10000.0, abs=0.01)
    state, closed, _ = broker.on_new_bar(
        state, {key: _bar("2026-06-03", 20030.0, 20210.0, 20020.0, 20180.0)})
    t = closed[0]
    assert t.exit_reason == "target"
    assert t.pnl == pytest.approx(pos.size * 50.0 * (20200.0 - 20012.0), abs=0.01)


def test_ac15_ledger_row_complete_with_r2_columns(tmp_path):
    path = str(tmp_path / "ledger.csv")
    state = _state()
    state, _, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
    state, closed, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1045, 1.1160, 1.1030, 1.1120)})
    broker.append_trades(closed, path)
    ledger = broker.load_ledger(path)
    assert list(ledger.columns) == LEDGER_COLUMNS
    row = ledger.iloc[0]
    for col in LEDGER_COLUMNS:
        assert row[col] is not None and str(row[col]) != ""
    # R2-07 自洽:risk_actual = size × |entry−stop|
    assert float(row["risk_actual"]) == pytest.approx(
        float(row["size"]) * abs(float(row["entry_price"]) - float(row["stop"])),
        abs=0.01)
    assert int(row["round"]) == 2


def test_ledger_append_only(tmp_path):
    path = str(tmp_path / "ledger.csv")
    state = _state()
    state, _, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
    state, closed, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1045, 1.1160, 1.1030, 1.1120)})
    broker.append_trades(closed, path)
    first = broker.load_ledger(path)
    broker.append_trades(closed, path)
    second = broker.load_ledger(path)
    assert len(second) == 2
    assert second.iloc[0].to_dict() == first.iloc[0].to_dict()


def test_reject_codes_validated(tmp_path):
    path = str(tmp_path / "rejects.csv")
    broker.append_rejects([{"date": "2026-06-02", "track": "tech",
                            "symbol": "EURUSD", "kind": "breakout",
                            "timeframe": "1d", "reason": "stop_too_tight_at_fill"}],
                          path)
    with pytest.raises(ValueError):
        broker.append_rejects([{"date": "d", "track": "tech", "symbol": "X",
                                "kind": "k", "timeframe": "1d",
                                "reason": "made_up_reason"}], path)


def test_reproducibility_no_randomness():
    def run():
        state = _state()
        trades = []
        state, c, _ = broker.on_new_bar(
            state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
        trades += c
        state, c, _ = broker.on_new_bar(
            state, {KEY: _bar("2026-06-03", 1.1045, 1.1160, 1.1030, 1.1120)})
        trades += c
        return [t.to_row() for t in trades], copy.deepcopy(state.equity)

    assert run() == run()


def test_state_roundtrip_with_r2_fields(tmp_path):
    state = _state()
    state, _, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
    broker.save_state(state, "2026-06-02 16:30", state_dir=str(tmp_path))
    loaded = broker.load_state(state_dir=str(tmp_path))
    assert len(loaded.positions) == 1
    p = loaded.positions[0]
    assert p.symbol == "EURUSD" and isinstance(p.symbol, str)
    assert p.entry_price == pytest.approx(1.1012)
    assert p.risk_actual == pytest.approx(300.0, abs=0.01)
    assert p.fill_rr == pytest.approx(2.2258, abs=0.0001)
    assert loaded.status_of("tech").status == "active"


def test_state_roundtrip_halt_date_empty_not_nan(tmp_path):
    """審查修正 F4:空 halt_date 讀回必須是空字串,不得變 'nan'。"""
    state = _state()
    broker.save_state(state, "2026-06-02 16:30", state_dir=str(tmp_path))
    loaded = broker.load_state(state_dir=str(tmp_path))
    assert loaded.status_of("tech").halt_date == ""
