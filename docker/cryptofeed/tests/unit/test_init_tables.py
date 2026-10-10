"""
Unit tests for the QuestDB table initialization constants. No server needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import init_questdb_tables as init  # noqa: E402


def test_trades_dedup_keys_include_the_designated_timestamp_and_trade_id():
    # QuestDB requires the designated timestamp among the keys; trade_id keeps distinct trades
    # that share a timestamp from being merged
    assert init.TRADES_DEDUP_KEYS[0] == "timestamp"
    assert "trade_id" in init.TRADES_DEDUP_KEYS
    assert {"exchange", "symbol"} <= set(init.TRADES_DEDUP_KEYS)


def test_dedup_sql_is_built_from_the_keys():
    assert init.TRADES_DEDUP_SQL == (
        "ALTER TABLE trades DEDUP ENABLE UPSERT KEYS(timestamp, exchange, symbol, trade_id)"
    )


def test_trades_has_contract_and_notional_columns():
    names = [name for name, _ in init.TRADES_EXTRA_COLUMNS]
    assert names == ["size_native", "size_unit", "contract_size", "contract_type", "notional_usd"]


def test_script_still_exposes_its_entry_points():
    # guards against an edit that drops a helper the constants-only tests would never touch
    assert callable(init.execute_sql)
    assert callable(init.init_tables)


def test_liquidity_table_has_every_metric_column():
    import perps

    ddl = init.PERPS_LIQUIDITY_DDL
    assert "PARTITION BY DAY" in ddl and "perps_liquidity" in ddl
    for column in perps.LIQUIDITY_DOUBLE_COLUMNS + perps.LIQUIDITY_LONG_COLUMNS:
        assert f"{column} " in ddl, column
    # the headline buckets and slippage sizes the dashboard reads
    for column in ("bid_usd_1bp", "ask_usd_100bp", "buy_slip_bps_1m", "sell_slip_bps_10k"):
        assert column in ddl


def test_liquidity_view_exists():
    assert "perps_liquidity_1m" in init.PERPS_LIQUIDITY_1M_DDL
    assert "SAMPLE BY 1m" in init.PERPS_LIQUIDITY_1M_DDL
    assert "avg(bid_usd_100bp)" in init.PERPS_LIQUIDITY_1M_DDL
    assert "min(bid_reach_bps)" in init.PERPS_LIQUIDITY_1M_DDL  # worst coverage, not an average


def test_ten_second_views_back_the_dashboard_windows():
    assert "perps_trades_10s" in init.PERPS_TRADES_10S_DDL
    assert "SAMPLE BY 10s" in init.PERPS_TRADES_10S_DDL
    assert "contract_type <> 'spot'" in init.PERPS_TRADES_10S_DDL
    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume_base",
        "notional_usd",
        "buy_notional_usd",
        "trades",
    ):
        assert f"AS {column}" in init.PERPS_TRADES_10S_DDL
    assert not hasattr(init, "PERPS_TRADES_1M_DDL")  # dropped: nothing read it
    assert "contract_type = 'spot'" in init.SPOT_TRADES_10S_DDL
    assert "SAMPLE BY 10s" in init.SPOT_TRADES_10S_DDL
    assert init.PERPS_TRADES_10S_TTL_SQL.startswith("ALTER MATERIALIZED VIEW perps_trades_10s")
