"""
QuestDB writer with async queue for high-throughput scenarios
Based on cryptofeed's SocketCallback pattern with BackendQueue
"""
import asyncio
import socket
import json
from collections import deque


class QuestDBWriterQueued:
    """
    QuestDB writer with async queue buffering for high-throughput ingestion
    
    Features:
    - Async queue to buffer writes during bursts
    - Background writer task for non-blocking writes
    - Automatic reconnection on connection failures
    - Batch writes for better performance
    """
    
    def __init__(self, host='127.0.0.1', port=9009, max_queue_size=10000, batch_size=100):
        self.host = host
        self.port = port
        self.sock = None
        self.max_queue_size = max_queue_size
        self.batch_size = batch_size
        self.queue = asyncio.Queue(maxsize=max_queue_size)
        self.running = True
        self.writer_task = None
        self._dropped_count = 0
        
        print(f"Initialized queued QuestDB writer (max_queue={max_queue_size}, batch={batch_size})")
    
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
            # Set TCP_NODELAY for lower latency
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            print(f"Connected to QuestDB at {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to connect to QuestDB: {e}")
            self.sock = None
    
    async def start(self):
        """Start the background writer task"""
        if not self.writer_task:
            self.writer_task = asyncio.create_task(self._writer_loop())
            print("Started background writer task")
    
    async def _writer_loop(self):
        """Background task that consumes queue and writes to QuestDB"""
        while self.running:
            try:
                # Ensure connection
                if not self.sock:
                    self._connect()
                    if not self.sock:
                        await asyncio.sleep(1)
                        continue
                
                # Collect batch of writes
                batch = []
                try:
                    # Wait for at least one item
                    item = await asyncio.wait_for(self.queue.get(), timeout=0.1)
                    batch.append(item)
                    
                    # Collect up to batch_size items without waiting
                    while len(batch) < self.batch_size:
                        try:
                            item = self.queue.get_nowait()
                            batch.append(item)
                        except asyncio.QueueEmpty:
                            break
                
                except asyncio.TimeoutError:
                    continue
                
                if batch:
                    # Write entire batch as one string
                    batch_data = "".join(batch)
                    try:
                        self.sock.sendall(batch_data.encode('utf-8'))
                    except (BrokenPipeError, ConnectionResetError, OSError) as e:
                        print(f"Socket error: {e}, reconnecting...")
                        self.sock = None
                        # Re-queue the batch
                        for item in batch:
                            try:
                                self.queue.put_nowait(item)
                            except asyncio.QueueFull:
                                self._dropped_count += 1
                        await asyncio.sleep(0.1)
            
            except Exception as e:
                print(f"Error in writer loop: {e}")
                await asyncio.sleep(0.1)
    
    async def write_trade(self, data):
        """Queue trade data for writing"""
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
            
            # Build ILP line
            table_name = "trades"
            tags = f"exchange={exchange},symbol={symbol},side={side},type={trade_type}"
            fields = f"price={price},amount={amount}"
            if trade_id is not None:
                fields += f",trade_id=\"{trade_id}\""
            
            ilp_line = f"{table_name},{tags} {fields} {timestamp_ns}\n"
            
            # Queue for writing
            try:
                self.queue.put_nowait(ilp_line)
            except asyncio.QueueFull:
                self._dropped_count += 1
                if self._dropped_count % 100 == 0:
                    print(f"Warning: Queue full, dropped {self._dropped_count} writes")
        
        except Exception as e:
            print(f"Error queueing trade: {e}")
    
    async def write_orderbook(self, book, receipt_timestamp, depth=10):
        """Queue orderbook snapshot for writing"""
        try:
            # Extract data
            exchange = book.exchange
            symbol = book.symbol
            timestamp = book.timestamp
            
            # Convert timestamps
            timestamp_ns = int(timestamp * 1_000_000_000) if timestamp else int(receipt_timestamp * 1_000_000_000)
            
            # Build field values
            fields = []
            
            # Bid levels
            for i in range(min(depth, len(book.book.bids))):
                bid_price, bid_size = book.book.bids.index(i)
                fields.append(f"bid_{i}_price={bid_price}")
                fields.append(f"bid_{i}_size={bid_size}")
            
            # Ask levels
            for i in range(min(depth, len(book.book.asks))):
                ask_price, ask_size = book.book.asks.index(i)
                fields.append(f"ask_{i}_price={ask_price}")
                fields.append(f"ask_{i}_size={ask_size}")
            
            # Build ILP line
            table_name = "orderbooks"
            tags = f"exchange={exchange},symbol={symbol}"
            fields_str = ",".join(fields)
            
            ilp_line = f"{table_name},{tags} {fields_str} {timestamp_ns}\n"
            
            # Queue for writing
            try:
                self.queue.put_nowait(ilp_line)
            except asyncio.QueueFull:
                self._dropped_count += 1
                if self._dropped_count % 1000 == 0:
                    print(f"Warning: Queue full, dropped {self._dropped_count} orderbook snapshots")
        
        except Exception as e:
            print(f"Error queueing orderbook: {e}")
    
    async def write_orderbook_compact(self, book, receipt_timestamp, depth=20):
        """
        Queue compact orderbook snapshot for writing
        
        Stores bids and asks as JSON arrays instead of expanding into individual columns.
        
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
            bids_json = json.dumps(bids)
            asks_json = json.dumps(asks)
            
            # Escape double quotes for ILP format
            bids_escaped = bids_json.replace('"', '\\"')
            asks_escaped = asks_json.replace('"', '\\"')
            
            # Build ILP line: table,tags fields timestamp
            tags = f"exchange={exchange},symbol={symbol}"
            fields = f'bids="{bids_escaped}",asks="{asks_escaped}"'
            
            ilp_line = f"{table_name},{tags} {fields} {timestamp_ns}\n"
            
            # Queue for writing
            try:
                self.queue.put_nowait(ilp_line)
            except asyncio.QueueFull:
                self._dropped_count += 1
                if self._dropped_count % 1000 == 0:
                    print(f"Warning: Queue full, dropped {self._dropped_count} compact orderbook snapshots")
        
        except Exception as e:
            print(f"Error queueing compact orderbook: {e}")
    
    def get_queue_size(self):
        """Get current queue size"""
        return self.queue.qsize()
    
    def get_dropped_count(self):
        """Get count of dropped writes"""
        return self._dropped_count
    
    async def stop(self):
        """Stop the writer and close connection"""
        print("Stopping queued writer...")
        self.running = False
        if self.writer_task:
            await self.writer_task
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None
        print(f"Writer stopped. Dropped {self._dropped_count} writes.")
