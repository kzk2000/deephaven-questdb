# Makefile for Deephaven-QuestDB project

.PHONY: help build build_with_drop up down clean rebuild logs ps test format

help:
	@echo "Deephaven-QuestDB Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make build            - Build all Docker images"
	@echo "  make build_with_drop  - Drop QuestDB tables and build images"
	@echo "  make up               - Start all services"
	@echo "  make rebuild          - Clean build and start (recommended for first setup)"
	@echo ""
	@echo "Management:"
	@echo "  make down      - Stop all services"
	@echo "  make restart   - Restart all services"
	@echo "  make clean     - Stop and remove all data (destructive!)"
	@echo ""
	@echo "Monitoring:"
	@echo "  make ps        - Show service status"
	@echo "  make logs      - Show all logs"
	@echo "  make logs-dh   - Show Deephaven logs"
	@echo "  make logs-qdb  - Show QuestDB logs"
	@echo "  make logs-cf   - Show Cryptofeed logs"
	@echo ""
	@echo "Testing:"
	@echo "  make test      - Verify all services are healthy"
	@echo "  make urls      - Show all service URLs"
	@echo ""
	@echo "Development:"
	@echo "  make format    - Format Python code with ruff"

# Build all images
build:
	docker compose build

# Build with drop tables (drops QuestDB tables before building)
build_with_drop:
	@echo "Dropping QuestDB tables..."
	@curl -s "http://localhost:9000/exec?query=DROP%20MATERIALIZED%20VIEW%20IF%20EXISTS%20orderbooks_1s" >/dev/null 2>&1 || true
	@curl -s "http://localhost:9000/exec?query=DROP%20TABLE%20IF%20EXISTS%20orderbooks" >/dev/null 2>&1 || true
	@curl -s "http://localhost:9000/exec?query=DROP%20TABLE%20IF%20EXISTS%20trades" >/dev/null 2>&1 || true
	@echo "✓ Tables dropped"
	@echo ""
	@echo "Building images..."
	docker compose build

# Start all services
up:
	docker compose up -d
	@echo ""
	@echo "✓ Services starting..."
	@echo "  Wait 30 seconds for full startup"
	@echo ""
	@echo "Access points:"
	@echo "  Deephaven: http://localhost:10000"
	@echo "  QuestDB:   http://localhost:9000"

# Stop all services
down:
	docker compose down

# Clean build and start (recommended for first setup)
rebuild:
	@echo "Stopping services..."
	docker compose down -v
	@echo ""
	@echo "Building images (this may take a few minutes)..."
	docker compose build --no-cache
	@echo ""
	@echo "Starting services..."
	docker compose up -d
	@echo ""
	@echo "✓ Services starting..."
	@echo "  Wait 30 seconds for full startup"
	@echo ""
	@echo "Verify with: make test"

# Restart all services
restart:
	docker compose down
	docker compose up -d

# Clean everything (removes data!)
clean:
	@echo "WARNING: This will delete all data!"
	@read -p "Are you sure? [y/N] " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		docker compose down -v; \
		echo "✓ All services and data removed"; \
	else \
		echo "Cancelled"; \
	fi

# Show service status
ps:
	docker compose ps

# Show all logs
logs:
	docker compose logs --tail=50 -f

# Show Deephaven logs
logs-dh:
	docker compose logs --tail=100 -f deephaven_qdb

# Show QuestDB logs
logs-qdb:
	docker compose logs --tail=100 -f questdb

# Show Cryptofeed logs
logs-cf:
	docker compose logs --tail=100 -f cryptofeed

# Test service health
test:
	@echo "Checking service health..."
	@docker compose ps
	@echo ""
	@echo "Testing connectivity..."
	@curl -s "http://localhost:9000/exec?query=SELECT%20count%28%2A%29%20FROM%20trades" 2>/dev/null | grep -q "count" && echo "✓ QuestDB responding" || echo "✗ QuestDB not ready"
	@curl -s http://localhost:10000 >/dev/null && echo "✓ Deephaven responding" || echo "✗ Deephaven not ready"
	@echo ""
	@echo "Testing tables..."
	@curl -s "http://localhost:9000/exec?query=SELECT%20count%28%2A%29%20FROM%20trades" 2>/dev/null | grep -q "count" && echo "✓ trades table exists" || echo "✗ trades table not ready"
	@curl -s "http://localhost:9000/exec?query=SELECT%20count%28%2A%29%20FROM%20orderbooks" 2>/dev/null | grep -q "count" && echo "✓ orderbooks table exists" || echo "✗ orderbooks table not ready"
	@curl -s "http://localhost:9000/exec?query=SELECT%20count%28%2A%29%20FROM%20orderbooks_1s" 2>/dev/null | grep -q "count" && echo "✓ orderbooks_1s view exists" || echo "✗ orderbooks_1s view not ready"
	@echo ""
	@echo "Table row counts:"
	@python3 -c "import sys; sys.path.insert(0, 'docker/cryptofeed/src'); from questdb_writer import QuestDBWriter; w = QuestDBWriter('localhost', verbose=False); r = w.execute_sql('SELECT count() FROM trades'); print(f\"  trades:        {r['dataset'][0][0]:>6} rows\"); r = w.execute_sql('SELECT count() FROM orderbooks'); print(f\"  orderbooks:    {r['dataset'][0][0]:>6} rows\"); r = w.execute_sql('SELECT count() FROM orderbooks_1s'); print(f\"  orderbooks_1s: {r['dataset'][0][0]:>6} rows\"); w.close()" 2>/dev/null || echo "  (Python check failed)"

# Show service URLs
urls:
	@echo "Service URLs:"
	@echo "  Deephaven:       http://localhost:10000"
	@echo "  QuestDB Web UI:  http://localhost:9000"
	@echo ""
	@echo "Database Connections:"
	@echo "  QuestDB PostgreSQL: localhost:8812 (user=admin, pass=quest)"
	@echo "  QuestDB ILP:        localhost:9009"

# Format Python code with ruff (via uvx)
format:
	uvx ruff format --no-cache
