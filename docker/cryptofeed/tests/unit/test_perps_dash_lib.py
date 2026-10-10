"""
Unit tests for perps_dash_lib (order book liquidity math for the perps dashboard). No server.
"""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

notebooks = Path(__file__).parent.parent.parent.parent.parent / "data" / "deephaven" / "notebooks"
sys.path.insert(0, str(notebooks))

import perps_dash_lib as lib  # noqa: E402

# A symmetric toy book around mid 100: levels every 0.1 (10 bp), 1 coin each = ~$100 per level
BID_PX = np.array([99.9, 99.8, 99.7, 99.6])
ASK_PX = np.array([100.1, 100.2, 100.3, 100.4])
ONES = np.ones(4)


def test_clean_side_sorts_and_drops_bad_levels():
    px, sz = lib.clean_side([99.0, np.nan, 100.0, -1.0, 98.0], [1, 1, 2, 1, 0], is_bid=True)
    assert px.tolist() == [100.0, 99.0]
    assert sz.tolist() == [2.0, 1.0]
    px, _ = lib.clean_side([101.0, 100.5], [1, 1], is_bid=False)
    assert px.tolist() == [100.5, 101.0]
    px, sz = lib.clean_side(None, None, True)
    assert px.size == 0 and sz.size == 0


def test_clean_side_drops_deephaven_null_sentinel():
    dh_null = -np.finfo(np.float64).max
    px, _ = lib.clean_side([100.0, dh_null], [1.0, 1.0], True)
    assert px.tolist() == [100.0]


def test_mid_and_spread():
    mid, spread = lib.mid_spread(BID_PX, ASK_PX)
    assert mid == pytest.approx(100.0)
    assert spread == pytest.approx(20.0)  # 0.2 / 100 = 20 bp
    mid, spread = lib.mid_spread(np.empty(0), ASK_PX)
    assert math.isnan(mid) and math.isnan(spread)


def test_depth_within_band_not_truncated():
    # 25 bp band below mid 100 -> bound 99.75: includes 99.9 and 99.8
    usd, truncated = lib.depth_within(BID_PX, ONES, 100.0, 25, is_bid=True)
    assert usd == pytest.approx(99.9 + 99.8)
    assert not truncated
    usd, truncated = lib.depth_within(ASK_PX, ONES, 100.0, 25, is_bid=False)
    assert usd == pytest.approx(100.1 + 100.2)
    assert not truncated


def test_depth_within_flags_truncation_when_book_exhausted():
    usd, truncated = lib.depth_within(BID_PX, ONES, 100.0, 100, is_bid=True)
    assert usd == pytest.approx(BID_PX.sum())
    assert truncated  # all 4 visible levels inside a 100 bp band: deeper depth unseen


def test_depth_within_empty_side():
    usd, truncated = lib.depth_within(np.empty(0), np.empty(0), 100.0, 10, True)
    assert math.isnan(usd) and truncated


def test_walk_book_buy_partial_level():
    # buy $150: 100.1 fully ($100.1), remaining $49.9 at 100.2
    avg, slip, filled, truncated = lib.walk_book(ASK_PX, ONES, 150.0, 100.0, is_bid=False)
    base = 1 + 49.9 / 100.2
    assert avg == pytest.approx(150.0 / base)
    assert slip == pytest.approx((150.0 / base / 100.0 - 1) * 1e4)
    assert slip > 10  # worse than the touch (10 bp from mid)
    assert filled == pytest.approx(150.0)
    assert not truncated


def test_walk_book_sell_is_positive_cost():
    avg, slip, filled, truncated = lib.walk_book(BID_PX, ONES, 50.0, 100.0, is_bid=True)
    assert avg == pytest.approx(99.9)
    assert slip == pytest.approx(10.0)  # selling at 99.9 vs mid 100 costs 10 bp
    assert not truncated


def test_walk_book_exact_level_boundary():
    avg, _, filled, truncated = lib.walk_book(ASK_PX, ONES, 100.1, 100.0, is_bid=False)
    assert avg == pytest.approx(100.1)
    assert filled == pytest.approx(100.1)
    assert not truncated


def test_walk_book_truncated_when_order_exceeds_book():
    avg, slip, filled, truncated = lib.walk_book(ASK_PX, ONES, 1e6, 100.0, is_bid=False)
    assert truncated
    assert filled == pytest.approx(ASK_PX.sum())
    assert avg == pytest.approx(ASK_PX.mean())
    assert slip == pytest.approx((ASK_PX.mean() / 100 - 1) * 1e4)


def test_parse_symbol():
    assert lib.parse_symbol("BTC-USD-PERP") == ("BTC", "USD", "inverse")
    assert lib.parse_symbol("ETH-USDT-PERP") == ("ETH", "USDT", "linear")
    assert lib.parse_symbol("BTC-USDC-PERP") == ("BTC", "USDC", "linear")
    with pytest.raises(ValueError):
        lib.parse_symbol("BTC-USD")


def test_family_rows_cover_all_venues():
    btc_inverse = {r["exchange"] for r in lib.family_rows("BTC", "inverse")}
    assert btc_inverse == {"DERIBIT", "OKX", "BYBIT"}
    btc_linear = {(r["exchange"], r["symbol"]) for r in lib.family_rows("BTC", "linear")}
    assert ("BINANCE_FUTURES", "BTC-USDT-PERP") in btc_linear
    assert ("BINANCE_FUTURES", "BTC-USDC-PERP") not in btc_linear  # USDT-M only
    assert ("DERIBIT", "BTC-USDC-PERP") in btc_linear
    assert ("HYPERLIQUID", "BTC-USDC-PERP") in btc_linear
    assert all(r["Ctype"] == "linear" for r in lib.family_rows("ETH", "linear"))
    hl = next(r for r in lib.instrument_rows() if r["exchange"] == "HYPERLIQUID")
    assert hl["StaleAfter"] > 15  # its book only arrives every ~6 s
    series = [r["Series"] for r in lib.instrument_rows()]
    assert len(series) == len(set(zip(series, [r["symbol"] for r in lib.instrument_rows()])))


def test_venue_status():
    assert lib.venue_status(None, None) == "NO DATA"
    assert lib.venue_status(float("nan"), None) == "NO DATA"
    assert lib.venue_status(1.0, None) == "LIVE"
    assert lib.venue_status(100.0, 1.0) == "STALE"
    assert lib.venue_status(None, 30.0) == "LIVE"


def test_formatting():
    assert lib.fmt_usd(1_234_567) == "$1.23M"
    assert lib.fmt_usd(45_600) == "$45.6k"
    assert lib.fmt_usd(None) == "-"
    assert lib.fmt_price(83012.345, "BTC") == "83,012.3"
    assert lib.fmt_price(2506.5, "ETH") == "2,506.50"
    assert lib.fmt_bps(1.234, signed=True) == "+1.23 bp"


def test_liquidity_row_matches_schema_and_primitives():
    row = dict(zip(lib.LIQ_COLUMNS, lib.liquidity_row(BID_PX, ONES, ASK_PX, ONES)))
    assert len(lib.LIQ_COLUMNS) == 8 + 2 * len(lib.BUCKETS_BPS) + 2 * len(lib.NOTIONALS)
    assert row["mid"] == pytest.approx(100.0)
    assert row["spread_bps"] == pytest.approx(20.0)
    assert row["bid_reach_bps"] == pytest.approx(40.0)
    assert row["bid_usd_1bp"] == 0.0  # nothing within 1 bp of mid (touch is 10 bp away)
    assert row["bid_usd_25bp"] == pytest.approx(99.9 + 99.8)
    assert row["ask_usd_100bp"] == pytest.approx(ASK_PX.sum())
    # the whole toy book is ~$400 per side: every listed notional is unfillable -> NaN (NULL)
    assert all(math.isnan(row[lib.slip_col(d, k)]) for d in ("buy", "sell") for k in lib.NOTIONALS)


def test_liquidity_row_fillable_slippage():
    big = np.full(4, 1_000.0)  # ~$100k per level
    row = dict(zip(lib.LIQ_COLUMNS, lib.liquidity_row(BID_PX, big, ASK_PX, big)))
    assert row["buy_slip_bps_10k"] == pytest.approx(10.0)  # filled entirely at the touch
    assert row["sell_slip_bps_50k"] == pytest.approx(10.0)
    assert row["buy_slip_bps_250k"] > 10.0
    assert math.isnan(row["buy_slip_bps_1m"])  # $400k visible: cannot fill $1M


def test_liquidity_row_empty_book():
    row = dict(zip(lib.LIQ_COLUMNS, lib.liquidity_row(None, None, None, None)))
    assert math.isnan(row["mid"]) and row["bid_levels"] == 0
    assert math.isnan(row["bid_usd_5bp"])


def test_lower_bound_and_common_coverage():
    assert lib.is_lower_bound(6.0, 10)
    assert not lib.is_lower_bound(6.0, 5)
    assert lib.is_lower_bound(None, 1)
    assert lib.common_coverage([4000.0, 6.2]) == [1, 2, 3, 5]
    assert lib.common_coverage([4000.0, float("nan")]) == list(lib.BUCKETS_BPS)
    assert lib.common_coverage([]) == []
    assert lib.common_coverage([0.5]) == []


def test_labels_and_ratio():
    assert lib.NOTIONAL_LABELS["1m"] == "$1M"
    assert lib.NOTIONAL_LABELS["250k"] == "$250k"
    assert lib.ratio_text(300, 100) == "3.0x"
    assert lib.ratio_text(50, 100) == "0.50x"
    assert lib.ratio_text(None, 100) == "-"


def test_every_venue_explains_its_book_coverage():
    for venue in lib.VENUES:
        assert venue.coverage_note, venue.exchange
        assert lib.coverage_note(venue.exchange) == venue.coverage_note
    assert "20 levels" in lib.coverage_note("BINANCE_FUTURES")
    assert "1000" in lib.coverage_note("BYBIT")
    assert lib.coverage_note("NOPE") == ""
