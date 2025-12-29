"""
Simulation test with mock live orderbook feed

This simulates a live feed by generating multiple orderbooks
and writing them to QuestDB in sequence.
"""

import sys
from pathlib import Path
import time
import random
from collections import OrderedDict

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

from questdb_writer import QuestDBWriter


class MockBook:
    """Mock orderbook with randomized prices"""

    def __init__(self, exchange, symbol, base_price=50000.0):
        self.exchange = exchange
        self.symbol = symbol
        self.timestamp = time.time()

        # Generate realistic bid/ask spread
        spread = base_price * 0.0001  # 0.01% spread

        self.book = type(
            "obj",
            (object,),
            {
                "bids": OrderedDict(
                    {base_price - spread * (i + 1): random.uniform(0.1, 2.0) for i in range(20)}
                ),
                "asks": OrderedDict(
                    {base_price + spread * (i + 1): random.uniform(0.1, 2.0) for i in range(20)}
                ),
            },
        )()


def test_simulated_orderbook_feed():
    """
    Test simulated live orderbook feed

    Generates orderbooks from multiple exchanges and writes them to QuestDB.
    Verifies that data is being written correctly.
    """
    print("\n" + "=" * 80)
    print("Starting simulated orderbook feed test")
    print("=" * 80)

    writer = QuestDBWriter(host="localhost", verbose=True)

    exchanges = ["Coinbase", "Kraken", "Bitstamp"]
    symbols = ["BTC-USD", "ETH-USD"]

    initial_count_result = writer.execute_sql("SELECT count() FROM orderbooks")
    initial_count = initial_count_result["dataset"][0][0]
    print(f"\nInitial orderbook count: {initial_count}")

    # Simulate 30 orderbook updates (10 iterations, 3 exchanges)
    print(f"\nSimulating orderbook feed...")
    updates = 0

    for iteration in range(10):
        for exchange in exchanges:
            for symbol in symbols:
                # Generate random base price
                if symbol == "BTC-USD":
                    base_price = 50000.0 + random.uniform(-1000, 1000)
                else:
                    base_price = 3000.0 + random.uniform(-100, 100)

                # Create and write orderbook
                book = MockBook(exchange, symbol, base_price)
                result = writer.write_orderbook(book, book.timestamp, depth=20)

                if result:
                    updates += 1
                    if updates % 10 == 0:
                        print(f"  ✓ Wrote {updates} orderbooks...")

        time.sleep(0.5)  # Small delay between iterations

    print(f"\n✓ Completed: Wrote {updates} orderbooks")

    # Wait for data to be committed
    time.sleep(2)

    # Verify data growth
    final_count_result = writer.execute_sql("SELECT count() FROM orderbooks")
    final_count = final_count_result["dataset"][0][0]

    growth = final_count - initial_count
    print(f"\nFinal orderbook count: {final_count}")
    print(f"Growth: +{growth} rows")

    # Verify we got the expected updates
    assert growth >= updates * 0.9, f"Expected at least {int(updates * 0.9)} new rows, got {growth}"

    # Show latest orderbooks per exchange/symbol
    print(f"\nLatest orderbooks:")
    query = """
        SELECT exchange, symbol, timestamp
        FROM orderbooks 
        LATEST ON timestamp PARTITION BY exchange, symbol
    """
    result = writer.execute_sql(query)

    for row in result["dataset"][:6]:
        exchange, symbol, timestamp = row
        print(f"  {exchange:10s} {symbol:10s} {timestamp}")

    writer.close()

    print("\n" + "=" * 80)
    print("✓ Simulation test passed")
    print("=" * 80)


if __name__ == "__main__":
    test_simulated_orderbook_feed()
