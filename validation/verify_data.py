#!/usr/bin/env python3
"""
Verify data in orderbooks_compact table
"""
import sys
sys.path.insert(0, 'docker/cryptofeed/src')

from questdb_writer import QuestDBWriter


def verify_data():
    writer = QuestDBWriter(host='127.0.0.1')
    
    print("📊 Querying orderbooks table...")
    print("=" * 100)
    
    # Simple query to count rows
    count_sql = "SELECT count() FROM orderbooks"
    result = writer.execute_sql(count_sql)
    
    if result and 'dataset' in result:
        count = result['dataset'][0][0]
        print(f"\n✅ Total rows in orderbooks: {count}")
    else:
        print(f"❌ Count query failed")
        return
    
    # Query recent data
    query_sql = """
        SELECT timestamp, exchange, symbol, bids, asks
        FROM orderbooks 
        ORDER BY timestamp DESC 
        LIMIT 10
    """
    result = writer.execute_sql(query_sql)
    
    if result and 'dataset' in result:
        rows = result['dataset']
        print(f"\n📋 Latest 10 rows:")
        print("-" * 100)
        
        for i, row in enumerate(rows, 1):
            timestamp, exchange, symbol, bids, asks = row
            bid_levels = len(bids[0]) if bids and len(bids) > 0 else 0
            ask_levels = len(asks[0]) if asks and len(asks) > 0 else 0
            
            print(f"\n{i}. {timestamp}")
            print(f"   Exchange: {exchange:12s} Symbol: {symbol:12s}")
            print(f"   Bid levels: {bid_levels:3d}  Ask levels: {ask_levels:3d}")
            
            if bids and len(bids) >= 2 and len(bids[0]) >= 3:
                print(f"   Top 3 bids: {bids[0][:3]}")
                print(f"   Volumes:    {bids[1][:3]}")
            
            if asks and len(asks) >= 2 and len(asks[0]) >= 3:
                print(f"   Top 3 asks: {asks[0][:3]}")
                print(f"   Volumes:    {asks[1][:3]}")
        
        print("\n" + "=" * 100)
        print("✅ Data verification complete!")
        
    else:
        print(f"❌ Query failed")


if __name__ == '__main__':
    verify_data()
