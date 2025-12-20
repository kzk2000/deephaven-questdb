#!/bin/bash
# Test runner script for cryptofeed QuestDB integration

set -e

echo "========================================"
echo "QuestDB Writer Test Suite"
echo "========================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if QuestDB is running
echo "Checking QuestDB availability..."
if timeout 5 curl -sf --max-time 3 http://localhost:9000/index.html > /dev/null 2>&1; then
    echo -e "${GREEN}✓ QuestDB is running${NC}"
else
    echo -e "${RED}✗ QuestDB is not running or not responding${NC}"
    echo "Please start QuestDB first: docker-compose up -d questdb"
    exit 1
fi

echo ""

# Check if pytest is available
if ! command -v pytest &> /dev/null; then
    echo -e "${YELLOW}Warning: pytest not found, using python -m pytest${NC}"
    PYTEST="python3 -m pytest"
else
    PYTEST="pytest"
fi

echo ""

# Run tests based on argument
case "${1:-all}" in
    unit)
        echo "Running unit tests..."
        $PYTEST unit/ -v
        ;;
    integration)
        echo "Running integration tests..."
        $PYTEST integration/ -v
        ;;
    simulation)
        echo "Running simulation tests..."
        $PYTEST simulation/ -v
        ;;
    all)
        echo "Running all tests..."
        $PYTEST . -v
        ;;
    *)
        echo "Usage: $0 [unit|integration|simulation|all]"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}========================================"
echo "All tests completed!"
echo "========================================${NC}"
