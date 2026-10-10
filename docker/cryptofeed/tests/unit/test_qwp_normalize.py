"""
Unit tests for qdb_qwp.normalize (QWP Arrow result -> Deephaven-friendly Arrow). No server needed.
"""

import json
import sys
from pathlib import Path

import pyarrow as pa

notebooks = Path(__file__).parent.parent.parent.parent.parent / "data" / "deephaven" / "notebooks"
sys.path.insert(0, str(notebooks))

from qdb_qwp import normalize  # noqa: E402


def _qwp_like_table():
    ts = pa.array(
        [1_700_000_000_000_000_000, 1_700_000_000_000_000_001], pa.timestamp("ns", tz="UTC")
    )
    symbols = pa.array(["COINBASE", "KRAKEN"]).dictionary_encode()
    books = pa.array([[[1.0, 2.0], [0.5, 0.25]], [[3.0], [4.0]]], pa.list_(pa.list_(pa.float64())))
    return pa.table({"timestamp": ts, "exchange": symbols, "bids": books, "price": [1.5, 2.5]})


def test_dictionary_symbols_become_strings():
    out = normalize(_qwp_like_table())
    assert out.schema.field("exchange").type == pa.string()
    assert out.column("exchange").to_pylist() == ["COINBASE", "KRAKEN"]


def test_timestamps_keep_nanosecond_utc():
    out = normalize(_qwp_like_table())
    assert out.schema.field("timestamp").type == pa.timestamp("ns", tz="UTC")
    assert out.column("timestamp").to_pylist() == _qwp_like_table().column("timestamp").to_pylist()


def test_nested_arrays_kept_by_default():
    out = normalize(_qwp_like_table())
    assert pa.types.is_list(out.schema.field("bids").type)


def test_nested_arrays_flatten_to_json_strings():
    out = normalize(_qwp_like_table(), flatten_arrays=True)
    assert out.schema.field("bids").type == pa.string()
    assert json.loads(out.column("bids")[0].as_py()) == [[1.0, 2.0], [0.5, 0.25]]
    assert out.column("price").to_pylist() == [1.5, 2.5]
