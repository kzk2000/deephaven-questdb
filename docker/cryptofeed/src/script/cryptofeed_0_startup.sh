#!/bin/bash

# Initialize QuestDB tables with TTL
echo "Initializing QuestDB tables..."
python /cryptofeed/src/init_questdb_tables.py questdb

# Start the first process
echo "Starting trades feed..."
python /cryptofeed/src/script/cryptofeed_1_trades.py &
TRADES_PID=$!

# Start the second process  
echo "Starting orderbooks feed..."
python /cryptofeed/src/script/cryptofeed_2_orderbooks.py &
ORDERBOOKS_PID=$!

# Function to cleanup background processes
cleanup() {
    echo "Stopping processes..."
    kill $TRADES_PID $ORDERBOOKS_PID 2>/dev/null
    wait $TRADES_PID $ORDERBOOKS_PID 2>/dev/null
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
    
    sleep 5
done