# T-03:AC-03(去重更新)、AC-04(部分失敗:其餘照常、失敗記錄、非0退出由管線層)
import pandas as pd

import config
from adapters import forex


def _bars(ts_list, price=1.1):
    return pd.DataFrame({
        "timestamp": ts_list,
        "open": [price] * len(ts_list),
        "high": [price + 0.01] * len(ts_list),
        "low": [price - 0.01] * len(ts_list),
        "close": [price] * len(ts_list),
    })


def test_ac03_merge_dedup_no_duplicate_timestamp():
    old = _bars(["2026-07-01T00:00:00", "2026-07-02T00:00:00"])
    fresh = _bars(["2026-07-02T00:00:00", "2026-07-03T00:00:00"], price=1.2)
    merged = forex.merge_history(old, fresh)
    assert list(merged["timestamp"]) == [
        "2026-07-01T00:00:00", "2026-07-02T00:00:00", "2026-07-03T00:00:00"]
    assert merged["timestamp"].is_unique
    # 既有棒不可被覆寫(歷史不可變)
    assert merged["close"].iloc[1] == 1.1


def test_ac04_partial_failure_others_still_update(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

    def fake_download(pair, tf):
        if pair == "USDJPY":
            raise RuntimeError("模擬資料源失敗")
        return _bars(["2026-07-01T00:00:00", "2026-07-02T00:00:00"])

    monkeypatch.setattr(forex, "_download", fake_download)
    histories, failures = forex.fetch_daily()
    ok_pairs = {p for (p, _tf) in histories}
    assert ok_pairs == {"EURUSD", "GBPUSD", "AUDUSD"}
    assert len(failures) == 1 and failures[0][0] == "USDJPY"
    assert (tmp_path / "fx_EURUSD_1d.csv").exists()
    assert not (tmp_path / "fx_USDJPY_1d.csv").exists()


def test_fetch_hourly_uses_1h_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(forex, "_download",
                        lambda pair, tf: _bars(["2026-07-01T09:00:00"]))
    histories, failures = forex.fetch_hourly()
    assert failures == []
    assert set(histories) == {(p, "1h") for p in config.FX_PAIRS}
    assert (tmp_path / "fx_EURUSD_1h.csv").exists()
