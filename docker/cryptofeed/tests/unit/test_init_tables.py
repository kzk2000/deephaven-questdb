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
