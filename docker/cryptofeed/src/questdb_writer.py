"""
QuestDB writer using the official py-questdb-client SDK

Unified writer supporting both trades and orderbooks using the official QuestDB Python client.
All data is sent via ILP protocol which supports arrays natively (requires QuestDB >= 9.0.0).

Usage:
    writer = QuestDBWriter(host='localhost')
    writer.write_trade(trade_data)           # Uses ILP via SDK
    writer.write_orderbook(book, timestamp)  # Uses ILP via SDK with numpy arrays
    writer.execute_sql("SELECT * FROM trades")  # Uses REST API
"""
import numpy as np
import urllib.request
import urllib.parse
import json
from questdb.ingress import Sender, TimestampNanos


class QuestDBWriter:
    """
    Unified QuestDB writer using the official SDK
    
    - ILP protocol via SDK for all data (trades and orderbooks)
    - Supports numpy arrays for orderbook price levels
    - REST API for SQL queries
    """
    
    def __init__(self, host='127.0.0.1', ilp_port=9000, protocol='http', verbose=True):
        """
        Initialize QuestDB writer with official SDK
        
        Args:
            host: QuestDB hostname (default: '127.0.0.1')
            ilp_port: ILP port - 9000 for HTTP (default), 9009 for TCP
            protocol: 'http' (recommended) or 'tcp' (default: 'http')
            verbose: Print initialization messages (default: True)
        """
        self.host = host
        self.ilp_port = ilp_port
        self.protocol = protocol
        self.verbose = verbose
        
        # Build configuration string for the SDK
        self.conf = f'{protocol}::addr={host}:{ilp_port};'
        
        # HTTP port for SQL queries (REST API)
        self.http_port = 9000
        self.base_url = f"http://{host}:{self.http_port}/exec"
        
        # Create sender and enter context (for persistent connection)
        self._sender = Sender.from_conf(self.conf)
        self._sender.__enter__()
        
        if verbose:
            print(f"QuestDB Writer (SDK) initialized:")
            print(f"  ILP:  {protocol.upper()} {host}:{ilp_port}")
            print(f"  REST: HTTP {host}:{self.http_port} (queries)")
    
    # =========================================================================
    # ILP Protocol Methods (for both trades and orderbooks)
    # =========================================================================
    
    def write_trade(self, data):
        """
        Write trade data to QuestDB using ILP protocol via SDK
        
        Args:
            data: Trade data dict with keys: exchange, symbol, side, price, amount, timestamp, etc.
        """
        try:
            # Extract data
            exchange = data['exchange']
            symbol = data['symbol']
            side = data['side']
            trade_type = data.get('type') or 'unknown'
            price = data['price']
            amount = data['amount']
            trade_id = data.get('id')
            timestamp = data.get('timestamp')
            receipt_timestamp = data['receipt_timestamp']
            
            # Convert timestamp to nanoseconds
            if timestamp:
                ts_nanos = TimestampNanos(int(timestamp * 1_000_000_000))
            else:
                ts_nanos = TimestampNanos(int(receipt_timestamp * 1_000_000_000))
            
            # Send via SDK (sender always exists from __init__)
            self._sender.row(
                'trades',
                symbols={
                    'exchange': exchange,
                    'symbol': symbol,
                    'side': side,
                    'type': trade_type
                },
                columns={
                    'price': price,
                    'size': amount,
                    'trade_id': trade_id if trade_id else ''
                },
                at=ts_nanos
            )
            
        except Exception as e:
            print(f"Error writing trade to QuestDB: {e}")
    
    # =========================================================================
    # Orderbook Method (using SDK with 2D numpy arrays)
    # =========================================================================
    
    def write_orderbook(self, book, receipt_timestamp, depth=20):
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
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Extract data
            exchange = book.exchange
            symbol = book.symbol
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
            
            if hasattr(book.book.bids, 'index'):
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
            
            if hasattr(book.book.asks, 'index'):
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
            bids_2d = np.array([bid_prices, bid_volumes], dtype=np.float64)
            asks_2d = np.array([ask_prices, ask_volumes], dtype=np.float64)
            
            # Send via SDK (sender always exists from __init__)
            self._sender.row(
                'orderbooks',
                symbols={'exchange': exchange, 'symbol': symbol},
                columns={'bids': bids_2d, 'asks': asks_2d},
                at=ts_nanos
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
            params = urllib.parse.urlencode({'query': sql})
            full_url = f"{self.base_url}?{params}"
            
            with urllib.request.urlopen(full_url) as response:
                result_text = response.read().decode()
                if result_text.strip() == '':
                    return {'ddl': 'OK'}
                
                result = json.loads(result_text)
                return result
        except Exception as e:
            print(f"Error executing SQL: {e}")
            return None
    
    # =========================================================================
    # Common Methods
    # =========================================================================
    
    def flush(self):
        """Flush buffered data"""
        if self._sender:
            self._sender.flush()
    
    def close(self):
        """Close SDK connection"""
        if self._sender:
            self._sender.__exit__(None, None, None)
            self._sender = None
            if self.verbose:
                print("QuestDB SDK connection closed")
