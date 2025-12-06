#!/bin/bash

# Initialize QuestDB tables with TTL
echo "Initializing QuestDB tables..."
python /cryptofeed/src/init_questdb_tables.py questdb

# Start the first process
python /cryptofeed/src/script/cryptofeed_1_trades.py &

# Start the second process
# python /cryptofeed/src/script/cryptofeed_2_orderbooks.py &

# Start the second process
python /cryptofeed/src/script/cryptofeed_3_orderbooks_compact.py &

# Wait for any process to exit
wait -n

# Exit with status of process that exited first
exit $?