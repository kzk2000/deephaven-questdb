#!/usr/bin/env python3
"""
Verify QuestDB orderbook data format and content
"""
import sys
from pathlib import Path

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / 'src'
sys.path.insert(0, str(src_path))

from questdb_writer import QuestDBWriter

def main():
    writer = QuestDBWriter('localhost')
    
    print("📊 Querying orderbooks table...")
    print("=" * 100)
    
    # Get total count
    result = writer.execute_sql("SELECT count() FROM orderbooks")
    total = result['dataset'][0][0]
    print(f"\n✅ Total rows in orderbooks: {total:,}")
    
    # Get latest 10 rows
    print("\n📋 Latest 10 rows:")
    print("-" * 100)
    
    query = """
        SELECT timestamp, exchange, symbol, bids, asks
        FROM orderbooks 
        ORDER BY timestamp DESC 
        LIMIT 10
    """
    result = writer.execute_sql(query)
    
    for i, row in enumerate(result['dataset'], 1):
        timestamp, exchange, symbol, bids, asks = row
        
        print(f"\n{i}. {timestamp}")
        print(f"   Exchange: {exchange:10s} Symbol: {symbol:12s}")
        
        if bids and len(bids) >= 2:
            print(f"   Bid levels:  {len(bids[0]):2d}  Ask levels:  {len(asks[0]):2d}")
            # Show top 3 bids
            print(f"   Top 3 bids: {bids[0][:3]}")
            print(f"   Volumes:    {bids[1][:3]}")
            # Show top 3 asks
            print(f"   Top 3 asks: {asks[0][:3]}")
            print(f"   Volumes:    {asks[1][:3]}")
        else:
            print(f"   Empty orderbook")
    
    print("\n" + "=" * 100)
    print("✅ Data verification complete!")
    
    writer.close()

if __name__ == '__main__':
    main()
