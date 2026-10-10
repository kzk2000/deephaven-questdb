#!/bin/bash

# Initialize QuestDB tables with TTL
echo "Initializing QuestDB tables..."
python /cryptofeed/src/init_questdb_tables.py questdb

# Wait to ensure tables are fully initialized before ILP connections
echo "Waiting for tables to be fully initialized..."
sleep 2

# Start the first process
echo "Starting trades feed..."
python /cryptofeed/src/script/cryptofeed_1_trades.py &
TRADES_PID=$!

# Start the second process  
echo "Starting orderbooks feed..."
python /cryptofeed/src/script/cryptofeed_2_orderbooks.py &
ORDERBOOKS_PID=$!

# Start the perpetual futures process (Binance / Bybit / Deribit / OKX, BTC and ETH)
echo "Starting perpetuals feed..."
python /cryptofeed/src/script/cryptofeed_3_perps.py &
PERPS_PID=$!

# Function to cleanup background processes
cleanup() {
    echo "Stopping processes..."
    kill $TRADES_PID $ORDERBOOKS_PID $PERPS_PID 2>/dev/null
    wait $TRADES_PID $ORDERBOOKS_PID $PERPS_PID 2>/dev/null
    echo "All processes stopped."
}

# Set trap to cleanup on container stop
trap cleanup SIGTERM SIGINT

# Monitor processes
while true; do
    if ! kill -0 $TRADES_PID 2>/dev/null; then
        echo "Trades process died, restarting..."
        python /cryptofeed/src/script/cryptofeed_1_trades.py &
        TRADES_PID=$!
    fi
    
    if ! kill -0 $ORDERBOOKS_PID 2>/dev/null; then
        echo "Orderbooks process died, restarting..."
        python /cryptofeed/src/script/cryptofeed_2_orderbooks.py &
        ORDERBOOKS_PID=$!
    fi
    
    if ! kill -0 $PERPS_PID 2>/dev/null; then
        echo "Perpetuals process exited, restarting..."
        python /cryptofeed/src/script/cryptofeed_3_perps.py &
        PERPS_PID=$!
    fi

    sleep 5
done