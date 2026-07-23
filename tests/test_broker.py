# T-08:AC-14(fixture 行情逐K推進,人工驗算)、AC-15(台帳欄位完整)
# + append-only 保護、同K雙觸發以停損計、跳空不進場、可重現性
import copy

import pytest

import config
from core import broker
from core.models import LEDGER_COLUMNS, Order, Plan, PortfolioState, Signal


def _order(track="tech", symbol="EURUSD", direction="long",
           entry=1.1000, stop=1.0950, target=1.1100, size=30000.0):
    s = Signal(track=track, symbol=symbol, date="2026-06-01", kind="breakout",
               direction=direction)
    plan = Plan(signal=s, entry_ref=entry, stop=stop, target=target, rr=2.0)
    return Order(plan=plan, size=size, status="approved")


def _bar(ts, o, h, l, c):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c}


def _state():
    return PortfolioState(equity=dict(config.CAPITAL), peak_equity=dict(config.CAPITAL))


KEY = ("EURUSD", "1d")


def test_ac14_entry_and_target_exit_manual_case():
    """人工驗算:開盤 1.1010 + 滑價 0.0002 → 進場 1.1012;
    次K高 1.1105 觸目標 1.1100 → 出場 1.1100(target),
    R = 0.0088/0.0062 = 1.4194,pnl = 30000×0.0088 = 264.00 USD。"""
    state = _state()
    state, closed = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
    assert closed == [] and len(state.positions) == 1
    pos = state.positions[0]
    assert pos.entry_price == pytest.approx(1.1012)

    state, closed = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1045, 1.1105, 1.1030, 1.1090)})
    assert len(closed) == 1 and state.positions == []
    t = closed[0]
    assert t.exit_reason == "target"
    assert t.exit_price == pytest.approx(1.1100)
    assert t.r_multiple == pytest.approx(1.4194, abs=0.0001)
    assert t.pnl == pytest.approx(264.00, abs=0.01)
    assert state.equity["tech"] == pytest.approx(config.CAPITAL["tech"] + 264.00, abs=0.01)


def test_same_bar_double_trigger_counts_as_stop():
    state = _state()
    state, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1020, 1.1000, 1.1010)}, [_order()])
    # 同K同時觸停損與目標 → 以停損計(紅線 5)
    state, closed = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1010, 1.1200, 1.0900, 1.1150)})
    assert closed[0].exit_reason == "stop"
    assert closed[0].exit_price == pytest.approx(1.0950)


def test_gap_through_stop_no_fill():
    state = _state()
    state, closed = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.0940, 1.0960, 1.0930, 1.0950)}, [_order()])
    assert state.positions == [] and closed == []


def test_max_hold_expiry_exits_at_close():
    state = _state()
    state, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1015, 1.1005, 1.1010)}, [_order()])
    closed = []
    for i in range(config.MAX_HOLD_BARS + 1):
        state, c = broker.on_new_bar(
            state, {KEY: _bar(f"2026-07-{i + 1:02d}", 1.1010, 1.1015, 1.1005, 1.1013)})
        closed.extend(c)
    assert len(closed) == 1
    assert closed[0].exit_reason == "invalidated"
    assert closed[0].exit_price == pytest.approx(1.1013)


def test_chip_pnl_in_twd():
    state = _state()
    order = _order(track="chip", symbol="MTX", entry=20000.0, stop=19900.0,
                   target=20200.0, size=2.0)
    key = ("MTX", "1d")
    state, _ = broker.on_new_bar(
        state, {key: _bar("2026-06-02", 20010.0, 20050.0, 19960.0, 20030.0)}, [order])
    assert state.positions[0].entry_price == pytest.approx(20012.0)  # +2 點滑價
    state, closed = broker.on_new_bar(
        state, {key: _bar("2026-06-03", 20030.0, 20210.0, 20020.0, 20180.0)})
    t = closed[0]
    assert t.exit_reason == "target"
    assert t.pnl == pytest.approx(2.0 * 50.0 * (20200.0 - 20012.0), abs=0.01)  # 18800 TWD


def test_ac15_ledger_row_complete(tmp_path):
    path = str(tmp_path / "ledger.csv")
    state = _state()
    state, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
    state, closed = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1045, 1.1105, 1.1030, 1.1090)})
    broker.append_trades(closed, path)
    ledger = broker.load_ledger(path)
    assert list(ledger.columns) == LEDGER_COLUMNS
    row = ledger.iloc[0]
    for col in LEDGER_COLUMNS:
        assert row[col] is not None and str(row[col]) != ""
    assert row["track"] == "tech" and row["kind"] == "breakout"


def test_ledger_append_only(tmp_path):
    path = str(tmp_path / "ledger.csv")
    state = _state()
    state, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
    state, closed = broker.on_new_bar(
        state, {KEY: _bar("2026-06-03", 1.1045, 1.1105, 1.1030, 1.1090)})
    broker.append_trades(closed, path)
    first = broker.load_ledger(path)
    broker.append_trades(closed, path)  # 再寫一次:只能附加
    second = broker.load_ledger(path)
    assert len(second) == 2
    assert second.iloc[0].to_dict() == first.iloc[0].to_dict()  # 歷史列不可變


def test_reproducibility_no_randomness():
    def run():
        state = _state()
        trades = []
        state, c = broker.on_new_bar(
            state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
        trades += c
        state, c = broker.on_new_bar(
            state, {KEY: _bar("2026-06-03", 1.1045, 1.1105, 1.1030, 1.1090)})
        trades += c
        return [t.to_row() for t in trades], copy.deepcopy(state.equity)

    assert run() == run()


def test_state_roundtrip(tmp_path, monkeypatch):
    state = _state()
    state, _ = broker.on_new_bar(
        state, {KEY: _bar("2026-06-02", 1.1010, 1.1050, 1.0990, 1.1040)}, [_order()])
    broker.save_state(state, "2026-06-02 16:30", state_dir=str(tmp_path))
    loaded = broker.load_state(state_dir=str(tmp_path))
    assert len(loaded.positions) == 1
    p = loaded.positions[0]
    assert p.symbol == "EURUSD" and isinstance(p.symbol, str)
    assert p.entry_price == pytest.approx(1.1012)
    assert loaded.equity["tech"] == pytest.approx(config.CAPITAL["tech"])
