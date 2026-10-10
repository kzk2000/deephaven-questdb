# Makefile for Deephaven-QuestDB project

# bash is required for `read -n` and `[[ ]]` in the clean target
SHELL := /bin/bash

.PHONY: help build build_with_drop up down restart clean rebuild ps logs logs-dh logs-qdb logs-cf test pytest urls format lock-cryptofeed lock-deephaven

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
	@echo "  make pytest    - Run the Python test suite (pinned tool versions)"
	@echo "  make lock-cryptofeed / lock-deephaven - Regenerate pinned dependency sets"

# Build all images
build:
	docker compose build

# Build with drop tables (drops QuestDB tables before building)
build_with_drop:
	@echo "Dropping QuestDB tables..."
	@# views over trades / orderbooks first (init_questdb_tables.py recreates them)
	@for v in orderbooks_1s perps_trades_10s spot_trades_10s; do \
		curl -s "http://localhost:9000/exec?query=DROP%20MATERIALIZED%20VIEW%20IF%20EXISTS%20$$v" >/dev/null 2>&1 || true; \
	done
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
	docker compose logs --tail=100 -f deephaven

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
	@python3 -c "import json, urllib.parse, urllib.request as u; q = lambda t: json.load(u.urlopen('http://localhost:9000/exec?query=' + urllib.parse.quote('SELECT count() FROM ' + t)))['dataset'][0][0]; [print(f'  {t:<14} {q(t):>9} rows') for t in ('trades', 'orderbooks', 'orderbooks_1s')]" 2>/dev/null || echo "  (row count query failed - is QuestDB up?)"

# Python tests (unit + integration + simulation; needs the stack running for the latter two).
# Tool and library versions are pinned so the result does not depend on what is latest today.
pytest:
	uvx --with numpy==2.5.3 --with questdb==5.0.0 --with pyarrow==26.0.0 pytest@9.1.1 docker/cryptofeed/tests -q

# Regenerate the pinned dependency sets. Deliberate upgrades only: review the diff, rebuild, run tests.
lock-cryptofeed:
	docker run --rm -v "$(CURDIR)/docker/cryptofeed/requirements.txt:/r.txt:ro" python:3.13.16-bookworm \
		sh -c "pip install -q -r /r.txt setuptools wheel && pip freeze --exclude-editable | grep -v '^src'" \
		> docker/cryptofeed/constraints.txt.new && mv docker/cryptofeed/constraints.txt.new docker/cryptofeed/constraints.txt
	@echo "Updated docker/cryptofeed/constraints.txt (re-add the header comment if you want it back)"

lock-deephaven:
	docker run --rm --entrypoint sh ghcr.io/deephaven/server-ui:42.6 -c \
		"V=/opt/deephaven/venv/bin; \$$V/pip freeze | sort > /tmp/base && \$$V/pip install -q questdb connectorx SQLAlchemy psycopg2-binary deephaven-plugin-tradingview-lightweight && \$$V/pip freeze | sort | comm -13 /tmp/base -" \
		> docker/deephaven/constraints.txt.new && mv docker/deephaven/constraints.txt.new docker/deephaven/constraints.txt
	@echo "Updated docker/deephaven/constraints.txt (re-add the header comment if you want it back)"

# Show service URLs
urls:
	@echo "Service URLs:"
	@echo "  Deephaven:       http://localhost:10000"
	@echo "  QuestDB Web UI:  http://localhost:9000"
	@echo ""
	@echo "Database Connections:"
	@echo "  QuestDB PostgreSQL:      localhost:8812 (user=admin, pass=quest)"
	@echo "  QuestDB QWP (WebSocket): localhost:9000 (cryptofeed ingest)"
	@echo "  QuestDB ILP (TCP):       localhost:9009 (exposed, unused)"

# Format Python code with ruff (via uvx)
format:
	uvx ruff@0.17.0 format --no-cache
