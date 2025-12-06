"""
Simple QuestDB writer using raw TCP socket and ILP protocol
Note: py-questdb-client SDK requires context managers which don't work well in async callbacks
"""
import socket
import json


class QuestDBWriter:
    """QuestDB writer using raw TCP socket with ILP protocol"""
    
    def __init__(self, host='127.0.0.1', port=9009):
        self.host = host
        self.port = port
        self.sock = None
        self._connect()
    
    def _connect(self):
        """Connect to QuestDB"""
        try:
            if self.sock:
                try:
                    self.sock.close()
                except:
                    pass
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            print(f"Connected to QuestDB at {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to connect to QuestDB: {e}")
            self.sock = None
    
    def write_trade(self, data):
        """
        Write trade data to QuestDB using raw ILP format
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
            
            # Convert timestamps
            timestamp_ns = int(timestamp * 1_000_000_000) if timestamp else int(receipt_timestamp * 1_000_000_000)
            
            # Single table for all exchanges
            table_name = "trades"
            
            # Build ILP line: table,tags fields timestamp
            # Include exchange as a tag for better querying
            tags = f"exchange={exchange},symbol={symbol},side={side},type={trade_type}"
            fields = f"price={price},amount={amount}"
            if trade_id is not None:
                fields += f",trade_id=\"{trade_id}\""
            
            ilp_line = f"{table_name},{tags} {fields} {timestamp_ns}\n"
            
            # Send to QuestDB
            if self.sock:
                try:
                    self.sock.sendall(ilp_line.encode('utf-8'))
                except (BrokenPipeError, ConnectionResetError):
                    self._connect()
                    if self.sock:
                        self.sock.sendall(ilp_line.encode('utf-8'))
            else:
                self._connect()
                if self.sock:
                    self.sock.sendall(ilp_line.encode('utf-8'))
            
        except Exception as e:
            print(f"Error writing trade to QuestDB: {e}")
    
    def write_orderbook(self, book, receipt_timestamp, depth=10):
        """
        Write orderbook snapshot data to QuestDB using raw ILP format
        
        Args:
            book: Order book object from cryptofeed with book.book.bids and book.book.asks
            receipt_timestamp: Receipt timestamp in seconds (float)
            depth: Number of price levels to capture (default 10)
        """
        try:
            # Extract data
            exchange = book.exchange
            symbol = book.symbol
            timestamp = book.timestamp
            
            # Convert timestamps
            timestamp_ns = int(timestamp * 1_000_000_000) if timestamp else int(receipt_timestamp * 1_000_000_000)
            
            # Single table for all orderbooks
            table_name = "orderbooks"
            
            # Build field values for bid and ask levels
            fields = []
            
            # Capture bid levels (highest to lowest)
            for i in range(min(depth, len(book.book.bids))):
                bid_price, bid_size = book.book.bids.index(i)
                fields.append(f"bid_{i}_price={bid_price}")
                fields.append(f"bid_{i}_size={bid_size}")
            
            # Capture ask levels (lowest to highest)
            for i in range(min(depth, len(book.book.asks))):
                ask_price, ask_size = book.book.asks.index(i)
                fields.append(f"ask_{i}_price={ask_price}")
                fields.append(f"ask_{i}_size={ask_size}")
            
            # Build ILP line: table,tags fields timestamp
            tags = f"exchange={exchange},symbol={symbol}"
            fields_str = ",".join(fields)
            
            ilp_line = f"{table_name},{tags} {fields_str} {timestamp_ns}\n"
            
            # Send to QuestDB
            if self.sock:
                try:
                    self.sock.sendall(ilp_line.encode('utf-8'))
                except (BrokenPipeError, ConnectionResetError):
                    self._connect()
                    if self.sock:
                        self.sock.sendall(ilp_line.encode('utf-8'))
            else:
                self._connect()
                if self.sock:
                    self.sock.sendall(ilp_line.encode('utf-8'))
            
        except Exception as e:
            print(f"Error writing orderbook to QuestDB: {e}")
    
    def write_orderbook_compact(self, book, receipt_timestamp, depth=20):
        """
        Write orderbook snapshot in compact JSON format to QuestDB
        
        Stores bids and asks as JSON arrays instead of expanding into individual columns.
        This allows for flexible orderbook depth and more compact storage.
        
        Args:
            book: Order book object from cryptofeed with book.book.bids and book.book.asks
            receipt_timestamp: Receipt timestamp in seconds (float)
            depth: Number of price levels to capture (default 20, None = all available)
        """
        try:
            # Extract data
            exchange = book.exchange
            symbol = book.symbol
            timestamp = book.timestamp
            
            # Convert timestamps
            timestamp_ns = int(timestamp * 1_000_000_000) if timestamp else int(receipt_timestamp * 1_000_000_000)
            
            # Table name for compact format
            table_name = "orderbooks_compact"
            
            # Determine depth
            max_bids = len(book.book.bids)
            max_asks = len(book.book.asks)
            bid_depth = depth if depth is not None else max_bids
            ask_depth = depth if depth is not None else max_asks
            
            # Build bid and ask arrays as [price, size] pairs
            # Convert Decimal to float for JSON serialization
            bids = []
            for i in range(min(bid_depth, max_bids)):
                price, size = book.book.bids.index(i)
                bids.append([float(price), float(size)])
            
            asks = []
            for i in range(min(ask_depth, max_asks)):
                price, size = book.book.asks.index(i)
                asks.append([float(price), float(size)])
            
            # Serialize to JSON and escape for ILP format
            # ILP requires string fields to be double-quoted and internal quotes escaped
            bids_json = json.dumps(bids)
            asks_json = json.dumps(asks)
            
            # Escape double quotes for ILP format (use backslash)
            bids_escaped = bids_json.replace('"', '\\"')
            asks_escaped = asks_json.replace('"', '\\"')
            
            # Build ILP line: table,tags fields timestamp
            tags = f"exchange={exchange},symbol={symbol}"
            fields = f'bids="{bids_escaped}",asks="{asks_escaped}"'
            
            ilp_line = f"{table_name},{tags} {fields} {timestamp_ns}\n"
            
            # Send to QuestDB
            if self.sock:
                try:
                    self.sock.sendall(ilp_line.encode('utf-8'))
                except (BrokenPipeError, ConnectionResetError):
                    self._connect()
                    if self.sock:
                        self.sock.sendall(ilp_line.encode('utf-8'))
            else:
                self._connect()
                if self.sock:
                    self.sock.sendall(ilp_line.encode('utf-8'))
            
        except Exception as e:
            print(f"Error writing compact orderbook to QuestDB: {e}")
    
    def flush(self):
        """Flush is automatic with raw sockets"""
        pass
    
    def close(self):
        """Close connection"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
