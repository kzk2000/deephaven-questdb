"""
Cryptofeed configuration and utilities
Centralized configuration for symbols and debug callbacks
"""

# Symbols to subscribe to across all exchanges
SYMBOLS = ["BTC-USD", "ETH-USD", "AVAX-USD", "SOL-USD"]


def debug_callbacks():
    """[my_print] when CRYPTOFEED_PRINT is set, else []. Printing every trade and book to stdout
    (a book is hundreds of levels) costs CPU and can stall the event loop, so it is opt-in."""
    import os

    return [my_print] if os.environ.get("CRYPTOFEED_PRINT") else []


async def my_print(data, _receipt_time):
    """
    Debug callback to print data to console
    Useful for monitoring data flow
    """
    print(data)
