"""
Order book liquidity metrics: spread, depth within N bps, slippage. Hand-checkable books.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import perps  # noqa: E402
from perps import compute_liquidity  # noqa: E402

# bids 100 / 99 / 98, asks 101 / 102 / 103; sizes in base coin
BIDS = ([100.0, 99.0, 98.0], [1.0, 2.0, 3.0])
ASKS = ([101.0, 102.0, 103.0], [1.0, 1.0, 1.0])


def book(**kw):
    return compute_liquidity(BIDS[0], BIDS[1], ASKS[0], ASKS[1], **kw)


def test_mid_and_spread():
    m = book()
    assert m["mid"] == 100.5
    assert m["best_bid"] == 100.0 and m["best_ask"] == 101.0
    assert m["spread_bps"] == pytest.approx(1.0 / 100.5 * 1e4)


def test_depth_buckets_are_cumulative_usd_within_n_bps_of_mid():
    m = book(bps=(10, 50, 100, 150, 250))
    # distances from mid 100.5: bid 100 -> 49.75 bps, 99 -> 149.25, 98 -> 248.75
    assert m["bid_usd_10bp"] == 0.0  # nothing that close
    assert m["bid_usd_50bp"] == pytest.approx(100.0 * 1.0)
    assert m["bid_usd_100bp"] == pytest.approx(100.0)
    assert m["bid_usd_150bp"] == pytest.approx(100.0 + 99.0 * 2.0)
    assert m["bid_usd_250bp"] == pytest.approx(100.0 + 99.0 * 2.0 + 98.0 * 3.0)
    # asks: 101 -> 49.75, 102 -> 149.25, 103 -> 248.76
    assert m["ask_usd_50bp"] == pytest.approx(101.0)
    assert m["ask_usd_150bp"] == pytest.approx(101.0 + 102.0)
    assert m["ask_usd_250bp"] == pytest.approx(101.0 + 102.0 + 103.0)


def test_reach_tells_how_much_of_the_book_the_feed_published():
    m = book()
    assert m["bid_reach_bps"] == pytest.approx((100.5 - 98.0) / 100.5 * 1e4)
    assert m["ask_reach_bps"] == pytest.approx((103.0 - 100.5) / 100.5 * 1e4)
    assert m["bid_levels"] == 3 and m["ask_levels"] == 3


def test_slippage_within_the_first_level_is_the_half_spread():
    m = book(notionals=(50,))
    # a $50 buy fills entirely at 101: slippage vs mid = 0.5 / 100.5
    assert m["buy_slip_bps_50"] == pytest.approx(0.5 / 100.5 * 1e4)
    assert m["sell_slip_bps_50"] == pytest.approx(0.5 / 100.5 * 1e4)


def test_buy_walk_matches_a_manual_calculation():
    # $150: all of level 1 ($101 for 1 coin) + $49 at 102 = 0.480392 coin
    quantity = 1.0 + 49.0 / 102.0
    vwap = 150.0 / quantity
    expected = (vwap - 100.5) / 100.5 * 1e4
    got = compute_liquidity(*BIDS, *ASKS, notionals=(150,))["buy_slip_bps_150"]
    assert got == pytest.approx(expected)


def test_sell_walk_uses_the_bids_and_is_positive_cost():
    # $250: 100 USD at 100, then 150 USD of the 99 level
    quantity = 1.0 + 150.0 / 99.0
    vwap = 250.0 / quantity
    got = compute_liquidity(*BIDS, *ASKS, notionals=(250,))["sell_slip_bps_250"]
    assert got == pytest.approx((100.5 - vwap) / 100.5 * 1e4)
    assert got > 0


def test_unfillable_notional_is_none_not_a_number():
    m = book(notionals=(1_000_000,))
    assert m["buy_slip_bps_1m"] is None and m["sell_slip_bps_1m"] is None


def test_levels_may_arrive_unsorted():
    shuffled = compute_liquidity([98, 100, 99], [3, 1, 2], [103, 101, 102], [1, 1, 1], bps=(250,))
    assert shuffled["best_bid"] == 100.0 and shuffled["best_ask"] == 101.0
    assert shuffled["bid_usd_250bp"] == pytest.approx(book(bps=(250,))["bid_usd_250bp"])


def test_empty_or_crossed_books_return_none():
    assert compute_liquidity([], [], [101], [1]) is None
    assert compute_liquidity([100], [1], [], []) is None
    assert compute_liquidity([102], [1], [101], [1]) is None  # crossed
    assert compute_liquidity([100, 99], [0, 0], [101], [1]) is None  # no live size


def test_zero_size_levels_are_ignored():
    m = compute_liquidity([100, 99.9], [0.0, 1.0], [101], [1.0])
    assert m["best_bid"] == 99.9


def test_default_buckets_match_the_requested_set():
    assert perps.DEPTH_BPS == (1, 2, 3, 5, 10, 25, 50, 100)
    assert [perps.notional_label(n) for n in perps.SLIPPAGE_NOTIONALS] == [
        "10k",
        "50k",
        "100k",
        "250k",
        "500k",
        "1m",
        "5m",
    ]


def test_label_formats():
    assert [perps.notional_label(n) for n in (50, 999, 1000, 10_000, 250_000, 1_000_000)] == [
        "50",
        "999",
        "1k",
        "10k",
        "250k",
        "1m",
    ]


def test_column_names_cover_every_metric_the_function_returns():
    m = book()
    expected = set(perps.LIQUIDITY_DOUBLE_COLUMNS) | set(perps.LIQUIDITY_LONG_COLUMNS)
    assert set(m) == expected


def test_inverse_contract_depth_uses_base_coin_so_usd_is_price_times_size():
    # inverse: 8,300,000 USD notional at 83,000 is 100 coin; depth in USD must come back as 8.3m
    spec = perps.INVERSE_USD
    prices = np.array([83000.0, 82990.0])
    usd_sizes = np.array([8_300_000.0, 8_299_000.0])
    sizes = spec.to_base_array(prices, usd_sizes)
    m = compute_liquidity(prices, sizes, [83010.0], [1.0])
    assert m["bid_usd_100bp"] == pytest.approx(8_300_000.0 + 8_299_000.0)


def test_values_are_plain_python_types_the_questdb_sdk_accepts():
    # the SDK checks exact types: numpy.float64 is rejected even though it subclasses float
    m = compute_liquidity(*BIDS, *ASKS)
    for key, value in m.items():
        assert value is None or type(value) in (int, float), (key, type(value))
