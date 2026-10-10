"""
QuestDB writer using the official py-questdb-client SDK

Unified writer supporting both trades and orderbooks using the official QuestDB Python client.
Rows are sent one tick at a time; arrays are supported natively (requires QuestDB >= 9.0.0).

Protocols (``protocol`` argument or the QUESTDB_PROTOCOL environment variable):
    ws / wss   QWP over WebSocket (default). row() only appends to a buffer and a background
               sender publishes it every ``auto_flush_interval_ms`` (25 ms), so calls never
               block the asyncio event loop and rows are visible ~15-30 ms after the tick.
               Measured against ILP/HTTP with defaults: newest-row age 561 ms -> ~20 ms.
    http       ILP over HTTP. The SDK buffers for 1 s by default; flushing per tick instead
               blocks the calling thread on an HTTP round trip (and for seconds on an outage).
    tcp        ILP over TCP.

Usage:
    writer = QuestDBWriter(host='localhost')
    writer.write_trade(trade_data)
    writer.write_orderbook(book, timestamp)     # numpy 2D arrays
    writer.execute_sql("SELECT * FROM trades")  # REST API
"""

import itertools
import json
import os
import time
import urllib.parse
import urllib.request

import numpy as np
import questdb
from questdb import Sender, TimestampNanos


def _rejection_handler(counter):
    """Build a QWP error handler that holds only a counter, never the writer.

    A bound method (writer -> connection -> handler -> writer) creates a reference cycle that
    the SDK's background threads trip over when the interpreter shuts down.
    """

    def handler(error):
        counter[0] += 1
        print(f"QuestDB rejected data: {error}")

    return handler


DEFAULT_PROTOCOL = "ws"
# Short enough for near-real-time rows, long enough that a server outage of several seconds is
# buffered and replayed instead of back-pressuring row() (see docs: 10 ms stalled ~2.4 s in a
# 5 s outage, 25 ms did not).
DEFAULT_AUTO_FLUSH_INTERVAL_MS = 25


class QuestDBWriter:
    """
    Unified QuestDB writer using the official SDK

    - QWP (WebSocket) or ILP via the SDK for all data (trades and orderbooks)
    - Supports numpy arrays for orderbook price levels
    - REST API for SQL queries
    """

    def __init__(
        self,
        host="127.0.0.1",
        ilp_port=9000,
        protocol=None,
        verbose=True,
        http_port=9000,
        trades_table="trades",
        orderbooks_table="orderbooks",
        auto_flush_interval_ms=DEFAULT_AUTO_FLUSH_INTERVAL_MS,
    ):
        """
        Initialize QuestDB writer with official SDK

        Args:
            host: QuestDB hostname (default: '127.0.0.1')
            ilp_port: ingest port - 9000 for ws/http (default), 9009 for tcp
            protocol: 'ws' (QWP, default), 'wss', 'http' or 'tcp'. Defaults to the
                QUESTDB_PROTOCOL environment variable, then 'ws'
            auto_flush_interval_ms: how long a row may sit in the client buffer (ws only)
            verbose: Print initialization messages (default: True)
            http_port: HTTP port for SQL queries via REST (default: 9000)
            trades_table: Destination table for trades (default: 'trades')
            orderbooks_table: Destination table for orderbooks (default: 'orderbooks')
        """
        protocol = protocol or os.environ.get("QUESTDB_PROTOCOL") or DEFAULT_PROTOCOL
        self.host = host
        self.ilp_port = ilp_port
        self.protocol = protocol
        self.verbose = verbose
        self.trades_table = trades_table
        self.orderbooks_table = orderbooks_table
        # unique suffix for trades that arrive without an id (see write_trade)
        self._synthetic_ids = itertools.count(1)
        # rows/frames the server rejected (QWP reports these asynchronously)
        self._rejections = [0]
        self._error_handler = _rejection_handler(self._rejections)

        # Build configuration string for the SDK
        self.conf = f"{protocol}::addr={host}:{ilp_port};"
        if protocol in ("ws", "wss"):
            self.conf += f"auto_flush_interval={int(auto_flush_interval_ms)};"

        # HTTP port for SQL queries (REST API)
        self.http_port = http_port
        self.base_url = f"http://{host}:{self.http_port}/exec"

        # Create sender and enter context (for persistent connection)
        self._db = None
        if protocol in ("ws", "wss"):
            # QWP: a pooled connection handle that owns a background sender
            self._db = questdb.connect(self.conf, error_handler=self._error_handler)
            self._sender = self._db.sender()
        else:
            self._sender = Sender.from_conf(self.conf)
        self._sender.__enter__()

        if verbose:
            print("QuestDB Writer (SDK) initialized:")
            print(f"  ILP:  {protocol.upper()} {host}:{ilp_port}")
            print(f"  REST: HTTP {host}:{self.http_port} (queries)")

    @property
    def rejections(self):
        """Number of server-side rejections seen so far (QWP reports them asynchronously,
        not as exceptions from row())."""
        return self._rejections[0]

    # =========================================================================
    # Row writers (for both trades and orderbooks)
    # =========================================================================

    def write_trade(self, data):
        """
        Write trade data to QuestDB via the SDK sender (QWP by default)

        Args:
            data: Trade data dict with keys: exchange, symbol, side, price, size, timestamp, etc.
        """
        try:
            # Extract data
            exchange = data["exchange"]
            symbol = data["symbol"]
            side = data["side"]
            price = data["price"]
            size = data["amount"]
            trade_id = data.get("id")
            if trade_id is None or trade_id == "":
                # trades is deduplicated on (timestamp, exchange, symbol, trade_id) and a NULL id
                # would collapse distinct trades that share a timestamp. Use a unique id: not
                # idempotent on replay, but nothing is lost.
                trade_id = f"rx-{time.time_ns()}-{next(self._synthetic_ids)}"
            else:
                trade_id = str(trade_id)
            timestamp = data.get("timestamp")
            receipt_timestamp = data["receipt_timestamp"]

            # Exchange timestamp, falling back to receipt time (seconds -> nanoseconds)
            if timestamp:
                ts_nanos = TimestampNanos(int(timestamp * 1_000_000_000))
            else:
                ts_nanos = TimestampNanos(int(receipt_timestamp * 1_000_000_000))

            symbols = {
                "exchange": exchange,
                "symbol": symbol,
                "side": side,
            }
            columns = {
                "price": price,
                "size": size,
                "trade_id": trade_id,
            }
            # Contract columns. Derivative feeds pass their own (see perps.py: `size` is already
            # base coin and size_native / contract_size / notional_usd describe the venue's
            # number). Spot rows get the identity values so notional and volume queries work
            # across every row without special cases.
            size_native = data.get("size_native")
            columns["size_native"] = float(size if size_native is None else size_native)
            columns["contract_size"] = float(data.get("contract_size") or 1.0)
            notional = data.get("notional_usd")
            columns["notional_usd"] = float(price * size if notional is None else notional)
            symbols["size_unit"] = data.get("size_unit") or "BASE"
            symbols["contract_type"] = data.get("contract_type") or "spot"

            # Send via SDK (sender always exists from __init__)
            self._sender.row(
                self.trades_table,
                symbols=symbols,
                columns=columns,
                at=ts_nanos,
            )

        except Exception as e:
            print(f"Error writing trade to QuestDB: {e}")

    # =========================================================================
    # Orderbook Method (using SDK with 2D numpy arrays)
    # =========================================================================

    def write_orderbook(self, book, receipt_timestamp, depth=20, size_fn=None, symbol=None):
        """
        Write orderbook snapshot to QuestDB using SDK with 2D numpy arrays

        Sends data as DOUBLE[][] arrays using the official QuestDB Python SDK:
        - bids: 2D array [[prices...], [volumes...]]
        - asks: 2D array [[prices...], [volumes...]]

        This maintains compatibility with existing DOUBLE[][] schema.
        Requires QuestDB >= 9.0.0 and SDK >= 3.0.0 for array support.

        Args:
            book: Order book object from cryptofeed with book.book.bids and book.book.asks
            receipt_timestamp: Receipt timestamp in seconds (float)
            depth: Number of price levels to capture (default 20)
            size_fn: optional callable(prices, sizes) -> sizes applied to both sides, used to
                convert contract / USD sizes to base coin (see perps.SizeSpec.to_base_array)
            symbol: store the book under this symbol instead of book.symbol (venue aliases)

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Extract data
            exchange = book.exchange
            symbol = symbol or book.symbol
            timestamp = book.timestamp or receipt_timestamp

            # Convert to nanoseconds
            ts_nanos = TimestampNanos(int(timestamp * 1_000_000_000))

            # Determine depth
            max_bids = len(book.book.bids)
            max_asks = len(book.book.asks)
            bid_depth = min(depth, max_bids) if depth else max_bids
            ask_depth = min(depth, max_asks) if depth else max_asks

            # Extract top N levels
            # Note: book.bids and book.asks can be either:
            # - SortedDict from order_book library (cryptofeed live data)
            # - Regular dict/OrderedDict (tests)
            bid_prices = []
            bid_volumes = []

            if hasattr(book.book.bids, "index"):
                # SortedDict from cryptofeed
                for i in range(bid_depth):
                    price, volume = book.book.bids.index(i)
                    bid_prices.append(float(price))
                    bid_volumes.append(float(volume))
            else:
                # Regular dict - sort descending
                items = sorted(book.book.bids.items(), key=lambda x: x[0], reverse=True)[:bid_depth]
                bid_prices = [float(p) for p, _ in items]
                bid_volumes = [float(v) for _, v in items]

            ask_prices = []
            ask_volumes = []

            if hasattr(book.book.asks, "index"):
                # SortedDict from cryptofeed
                for i in range(ask_depth):
                    price, volume = book.book.asks.index(i)
                    ask_prices.append(float(price))
                    ask_volumes.append(float(volume))
            else:
                # Regular dict - sort ascending
                items = sorted(book.book.asks.items(), key=lambda x: x[0])[:ask_depth]
                ask_prices = [float(p) for p, _ in items]
                ask_volumes = [float(v) for _, v in items]

            # Create 2D numpy arrays: [[prices...], [volumes...]]
            # SDK v3.0.0+ supports n-dimensional arrays which creates DOUBLE[][] columns in QuestDB
            if size_fn is not None:
                bid_volumes = size_fn(np.array(bid_prices), np.array(bid_volumes))
                ask_volumes = size_fn(np.array(ask_prices), np.array(ask_volumes))
            bids_2d = np.array([bid_prices, bid_volumes], dtype=np.float64)
            asks_2d = np.array([ask_prices, ask_volumes], dtype=np.float64)

            # Send via SDK (sender always exists from __init__)
            self._sender.row(
                self.orderbooks_table,
                symbols={"exchange": exchange, "symbol": symbol},
                columns={"bids": bids_2d, "asks": asks_2d},
                at=ts_nanos,
            )

            return True

        except Exception as e:
            print(f"Error writing orderbook: {e}")
            import traceback

            traceback.print_exc()
            return False

    # =========================================================================
    # REST API for queries
    # =========================================================================

    def execute_sql(self, sql):
        """
        Execute SQL query via QuestDB HTTP REST API

        Args:
            sql: SQL query string

        Returns:
            dict: Query result with 'dataset', 'columns', etc., or {'ddl': 'OK'} for DDL
        """
        try:
            params = urllib.parse.urlencode({"query": sql})
            full_url = f"{self.base_url}?{params}"

            with urllib.request.urlopen(full_url) as response:
                result_text = response.read().decode()
                if result_text.strip() == "":
                    return {"ddl": "OK"}

                result = json.loads(result_text)
                return result
        except Exception as e:
            print(f"Error executing SQL: {e}")
            return None

    # =========================================================================
    # Common Methods
    # =========================================================================

    def write_row(self, table, symbols, columns, timestamp):
        """Write one arbitrary row (used for derived tables such as perps_liquidity).

        `columns` entries that are None are skipped (stored as NULL). `timestamp` is epoch
        seconds (float). Returns True on success.
        """
        try:
            self._sender.row(
                table,
                symbols=symbols,
                # numpy scalars are not accepted by the SDK; .item() gives the Python value
                columns={
                    k: (v.item() if hasattr(v, "item") else v)
                    for k, v in columns.items()
                    if v is not None
                },
                at=TimestampNanos(int(timestamp * 1_000_000_000)),
            )
            return True
        except Exception as e:
            print(f"Error writing row to {table}: {e}")
            return False

    def flush(self, wait=False):
        """Publish buffered rows. wait=True (ws only) also blocks until the server has
        acknowledged them, about 10 ms per call: use it at shutdown or in tests, not per tick."""
        if self._sender:
            if self._db is not None:
                self._sender.flush(wait=wait)
            else:
                self._sender.flush()

    def close(self):
        """Flush and close the SDK connection (safe to call more than once)"""
        if self._sender:
            try:
                self.flush(wait=True)
            except Exception as e:
                print(f"Error flushing QuestDB writer on close: {e}")
            self._sender.__exit__(None, None, None)
            self._sender = None
            if self._db is not None:
                self._db.close()
                self._db = None
            if self.verbose:
                print("QuestDB SDK connection closed")
