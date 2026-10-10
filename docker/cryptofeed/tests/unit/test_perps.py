"""
Unit tests for perpetual futures size normalization and venue spec building. No network.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import perps  # noqa: E402
from perps import BASE, INVERSE_USD, SizeSpec  # noqa: E402

# ---- conversion math -------------------------------------------------------------------------


def test_base_coin_sizes_pass_through():
    assert BASE.to_base(83000.0, 0.25) == 0.25


def test_inverse_usd_notional_becomes_coin_quantity():
    # Deribit BTC-PERPETUAL: amount 10000 is USD notional; at 83,095.5 that is ~0.12034 BTC
    assert INVERSE_USD.to_base(83095.5, 10000) == pytest.approx(10000 / 83095.5)


def test_linear_contracts_use_the_contract_value():
    # OKX BTC-USDT-SWAP: ctVal 0.01 BTC, 120 contracts -> 1.2 BTC
    spec = SizeSpec("base", 0.01, "CONTRACTS")
    assert spec.to_base(83107.6, 120) == pytest.approx(1.2)


def test_inverse_contracts_use_usd_per_contract_then_price():
    # OKX BTC-USD-SWAP: ctVal 100 USD, 108.4 contracts at 83,037.6
    spec = SizeSpec("inverse", 100.0, "CONTRACTS")
    assert spec.to_base(83037.6, 108.4) == pytest.approx(108.4 * 100 / 83037.6)


def test_contract_type():
    assert BASE.contract_type == "linear"
    assert INVERSE_USD.contract_type == "inverse"
    assert SizeSpec("base", 0.01, "CONTRACTS").contract_type == "linear"


def test_dollar_notional_for_inverse_and_linear():
    # inverse: 10,000 USD is 10,000 USD regardless of price
    assert INVERSE_USD.notional_usd(83000.0, 10000) == 10000
    # inverse contracts: 108.4 contracts x 100 USD
    assert SizeSpec("inverse", 100.0, "CONTRACTS").notional_usd(83037.6, 108.4) == pytest.approx(
        10840.0
    )
    # linear: base quantity x price (OKX BTC-USDT: 120 contracts x 0.01 BTC x 83,107.6)
    assert SizeSpec("base", 0.01, "CONTRACTS").notional_usd(83107.6, 120) == pytest.approx(
        1.2 * 83107.6
    )


def test_inverse_notional_equals_base_quantity_times_price():
    """The two views of an inverse trade must agree: notional == base * price."""
    spec = SizeSpec("inverse", 100.0, "CONTRACTS")
    price, contracts = 82917.3, 37.0
    assert spec.to_base(price, contracts) * price == pytest.approx(
        spec.notional_usd(price, contracts)
    )


def test_inverse_with_zero_price_does_not_divide_by_zero():
    assert INVERSE_USD.to_base(0.0, 100) == 0.0


def test_array_conversion_matches_scalar():
    prices = np.array([83000.0, 82999.5, 0.0])
    sizes = np.array([100.0, 250.0, 7.0])
    got = INVERSE_USD.to_base_array(prices, sizes)
    assert got[0] == pytest.approx(100 / 83000.0)
    assert got[1] == pytest.approx(250 / 82999.5)
    assert got[2] == 0.0  # guarded
    np.testing.assert_allclose(BASE.to_base_array(prices, sizes), sizes)


# ---- venue spec builders ---------------------------------------------------------------------

OKX_SAMPLE = {
    "data": [
        {"instId": "BTC-USD-SWAP", "ctType": "inverse", "ctVal": "100", "ctValCcy": "USD"},
        {"instId": "BTC-USDT-SWAP", "ctType": "linear", "ctVal": "0.01", "ctValCcy": "BTC"},
        {"instId": "ETH-USD-SWAP", "ctType": "inverse", "ctVal": "10", "ctValCcy": "USD"},
        {"instId": "ETH-USDT-SWAP", "ctType": "linear", "ctVal": "0.1", "ctValCcy": "ETH"},
    ]
}


def test_okx_specs_from_instrument_list():
    syms = ["BTC-USD-PERP", "BTC-USDT-PERP", "ETH-USD-PERP", "ETH-USDT-PERP", "SOL-USDT-PERP"]
    specs = perps.okx_specs(syms, fetch=lambda url: OKX_SAMPLE)
    assert specs["BTC-USD-PERP"] == SizeSpec("inverse", 100.0, "CONTRACTS")
    assert specs["BTC-USDT-PERP"] == SizeSpec("base", 0.01, "CONTRACTS")
    assert specs["ETH-USD-PERP"] == SizeSpec("inverse", 10.0, "CONTRACTS")
    assert specs["ETH-USDT-PERP"] == SizeSpec("base", 0.1, "CONTRACTS")
    assert "SOL-USDT-PERP" not in specs  # not listed


def test_deribit_bybit_and_binance_futures_specs_by_symbol_suffix():
    syms = ["BTC-USD-PERP", "BTC-USDC-PERP", "ETH-USD-PERP", "ETH-USDT-PERP"]
    for build in (perps.deribit_specs, perps.bybit_specs):
        specs = build(syms)
        assert specs["BTC-USD-PERP"] == INVERSE_USD
        assert specs["ETH-USD-PERP"] == INVERSE_USD
        assert specs["BTC-USDC-PERP"] == BASE
        assert specs["ETH-USDT-PERP"] == BASE
    assert set(perps.binance_futures_specs(["BTC-USDT-PERP"]).values()) == {BASE}


def test_build_specs_only_for_symbols_the_venue_lists():
    venue = perps.VENUES["deribit"]
    specs = perps.build_specs(venue, available=["BTC-USD-PERP", "SOL-USD-PERP"])
    assert list(specs) == ["BTC-USD-PERP"]
    assert perps.build_specs(venue, available=[]) == {}


def test_hyperliquid_perps_are_linear_base_coin_despite_the_usd_suffix():
    # cryptofeed calls them BTC-USD-PERP; inferring "inverse" from that suffix would divide a
    # base-coin size by the price
    venue = perps.VENUES["hyperliquid"]
    specs = perps.build_specs(venue, available=["BTC-USD-PERP", "ETH-USD-PERP", "DOGE-USD-PERP"])
    assert specs == {"BTC-USD-PERP": perps.BASE, "ETH-USD-PERP": perps.BASE}
    assert specs["BTC-USD-PERP"].contract_type == "linear"
    assert venue.stored_symbol("BTC-USD-PERP") == "BTC-USDC-PERP"
    assert venue.stored_symbol("ETH-USD-PERP") == "ETH-USDC-PERP"
    assert venue.stored_symbol("BTC-USDT-PERP") == "BTC-USDT-PERP"  # no alias: unchanged


def test_binance_is_usdt_margined_only():
    assert "binance_delivery" not in perps.VENUES  # COIN-M (inverse) is not wanted
    assert perps.VENUES["binance_futures"].symbols == ("BTC-USDT-PERP", "ETH-USDT-PERP")


def test_every_venue_wants_btc_and_eth_perpetuals_only():
    for venue in perps.VENUES.values():
        assert venue.symbols, venue.key
        assert all(s.endswith("-PERP") and s.split("-")[0] in ("BTC", "ETH") for s in venue.symbols)
    assert set(perps.DEFAULT_VENUES) == set(perps.VENUES)


def test_selected_venues_parsing():
    assert perps.selected_venues(None) == list(perps.DEFAULT_VENUES)
    assert perps.selected_venues(" Deribit , okx ") == ["deribit", "okx"]
    with pytest.raises(ValueError):
        perps.selected_venues("deribit,kraken_futures")
