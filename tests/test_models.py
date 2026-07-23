# T-01:資料模型欄位斷言 + 型別慣例(標的/日期為字串)
from core.models import LEDGER_COLUMNS, Plan, PortfolioState, Position, Signal, Trade


def test_signal_fields():
    s = Signal(track="chip", symbol="MTX", date="2026-07-23",
               kind="zscore", direction="long")
    assert isinstance(s.symbol, str) and isinstance(s.date, str)
    assert s.timeframe == "1d" and s.suppressed is False and s.meta == {}


def test_plan_five_fields_and_row():
    s = Signal(track="tech", symbol="EURUSD", date="2026-07-23",
               kind="breakout", direction="long")
    p = Plan(signal=s, entry_ref=1.1, entry_zone=(1.0998, 1.1002),
             stop=1.09, target=1.12, invalidation="次K開盤越過停損不進場", rr=2.0)
    row = p.to_row()
    for key in ("entry_zone", "stop", "target", "invalidation", "rr"):
        assert row[key] not in (None, "", 0.0) or key in ("stop",)
    assert row["signal_symbol"] == "EURUSD"


def test_trade_row_matches_ledger_columns():
    t = Trade(track="chip", symbol="MTX", timeframe="1d", kind="streak",
              direction="short", size=2.0, entry_date="2026-07-01",
              entry_price=20000.0, exit_date="2026-07-03", exit_price=19900.0,
              exit_reason="target", r_multiple=1.0, pnl=10000.0)
    assert list(t.to_row().keys()) == LEDGER_COLUMNS


def test_portfolio_state_counts_and_drawdown():
    st = PortfolioState(equity={"chip": 900_000.0}, peak_equity={"chip": 1_000_000.0})
    assert st.drawdown("chip") == 0.1
    st.positions = [
        Position(track="chip", symbol="MTX", timeframe="1d", kind="zscore",
                 direction="long", size=1.0, entry_date="2026-07-01",
                 entry_price=20000.0, stop=19900.0, target=20200.0),
        Position(track="tech", symbol="EURUSD", timeframe="1d", kind="breakout",
                 direction="long", size=1.0, entry_date="2026-07-01",
                 entry_price=1.1, stop=1.09, target=1.12),
    ]
    assert st.open_count() == 2
    assert st.open_count("chip") == 1
