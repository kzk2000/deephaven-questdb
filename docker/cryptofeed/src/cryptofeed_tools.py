"""
Cryptofeed configuration and utilities
Centralized configuration for symbols and debug callbacks
"""

# Symbols to subscribe to across all exchanges
SYMBOLS = ["BTC-USD", "ETH-USD", "AVAX-USD", "SOL-USD"]


async def my_print(data, _receipt_time):
    """
    Debug callback to print data to console
    Useful for monitoring data flow
    """
    print(data)
