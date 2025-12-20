-- Verification queries for orderbooks table
-- Run these in QuestDB console or via HTTP API

-- 1. Count total rows
SELECT count() as total_rows FROM orderbooks;

-- 2. Check latest data
SELECT * FROM orderbooks 
ORDER BY timestamp DESC 
LIMIT 10;

-- 3. Count by exchange and symbol
SELECT exchange, symbol, count() as row_count
FROM orderbooks
GROUP BY exchange, symbol
ORDER BY exchange, symbol;

-- 4. Check timestamp range
SELECT 
    min(timestamp) as earliest,
    max(timestamp) as latest,
    count() as total_rows
FROM orderbooks;

-- 5. Sample one row to verify array structure
SELECT timestamp, exchange, symbol, bids, asks
FROM orderbooks
LIMIT 1;

-- 6. Check materialized view
SELECT * FROM orderbooks_1s
ORDER BY timestamp DESC
LIMIT 10;
