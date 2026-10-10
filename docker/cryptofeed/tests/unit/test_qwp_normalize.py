"""
Unit tests for qdb_qwp.normalize (QWP Arrow result -> Deephaven-friendly Arrow). No server needed.
"""

import json
import sys
from pathlib import Path

import pyarrow as pa

notebooks = Path(__file__).parent.parent.parent.parent.parent / "data" / "deephaven" / "notebooks"
sys.path.insert(0, str(notebooks))

from qdb_qwp import (  # noqa: E402
    apply_array_dimensions,
    deephaven_type_hint,
    normalize,
    with_deephaven_hints,
)


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


def test_type_hint_for_nested_double_lists():
    assert deephaven_type_hint(pa.list_(pa.list_(pa.float64()))) == "double[][]"
    assert deephaven_type_hint(pa.list_(pa.list_(pa.list_(pa.int64())))) == "long[][][]"


def test_no_type_hint_for_flat_lists_and_scalars():
    assert deephaven_type_hint(pa.list_(pa.float64())) is None  # already maps to double[]
    assert deephaven_type_hint(pa.float64()) is None
    assert deephaven_type_hint(pa.string()) is None


def test_schema_hints_only_nested_fields():
    schema = pa.schema(
        [
            pa.field("price", pa.float64()),
            pa.field("bids", pa.list_(pa.list_(pa.float64()))),
        ]
    )
    hinted = with_deephaven_hints(schema)
    assert hinted.field("bids").metadata == {b"deephaven:type": b"double[][]"}
    assert hinted.field("price").metadata is None
    assert hinted.field("bids").type == schema.field("bids").type


def test_zero_row_array_schema_gets_true_dimensions():
    # a zero-row QWP result reports DOUBLE[][] as list<double>; table_columns() knows it is 2-D
    zero_row = pa.schema(
        [pa.field("bids", pa.list_(pa.float64())), pa.field("flat", pa.list_(pa.float64()))]
    )
    fixed = apply_array_dimensions(zero_row, {"bids": 2, "flat": 1, "missing": 3})
    assert fixed.field("bids").type == pa.list_(pa.list_(pa.float64()))
    assert fixed.field("flat").type == pa.list_(pa.float64())
    hinted = with_deephaven_hints(fixed)
    assert hinted.field("bids").metadata == {b"deephaven:type": b"double[][]"}
    assert hinted.field("flat").metadata is None
