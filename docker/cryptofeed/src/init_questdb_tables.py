"""
Initialize QuestDB tables with proper TTL settings at startup.
This script should be run before starting cryptofeed to ensure tables exist with correct configuration.
"""
import socket
import time
import sys


def execute_sql(host, port, sql):
    """Execute SQL via PostgreSQL wire protocol on port 8812"""
    import urllib.request
    import json
    
    # Use HTTP REST API
    url = f"http://{host}:9000/exec"
    params = urllib.parse.urlencode({'query': sql})
    full_url = f"{url}?{params}"
    
    try:
        with urllib.request.urlopen(full_url) as response:
            result = json.loads(response.read().decode())
            return result
    except Exception as e:
        print(f"Error executing SQL: {e}")
        return None


def init_tables(host='questdb', http_port=9000, wait_for_db=True, max_retries=30):
    """
    Initialize QuestDB tables with TTL settings
    
    Args:
        host: QuestDB hostname (default: 'questdb' for Docker)
        http_port: HTTP port for REST API (default: 9000)
        wait_for_db: Wait for database to be ready (default: True)
        max_retries: Maximum connection retry attempts (default: 30)
    """
    
    if wait_for_db:
        print(f"Waiting for QuestDB at {host}:{http_port}...")
        for attempt in range(max_retries):
            try:
                result = execute_sql(host, http_port, "SELECT 1")
                if result:
                    print("✅ QuestDB is ready!")
                    break
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  Attempt {attempt + 1}/{max_retries}: QuestDB not ready yet, waiting...")
                    time.sleep(2)
                else:
                    print(f"❌ Failed to connect to QuestDB after {max_retries} attempts")
                    sys.exit(1)
    
    print("\n📊 Initializing QuestDB tables...")
    
    # Configuration for tables
    tables_config = {
        'trades': {
            'ttl': None,  # Keep trades indefinitely
            'partition': 'DAY',
            'check_sql': "SELECT name FROM tables() WHERE name = 'trades'"
        },
        'orderbooks': {
            'ttl': '1 HOURS',  # Keep only last hour of expanded format
            'partition': 'HOUR',
            'check_sql': "SELECT name FROM tables() WHERE name = 'orderbooks'"
        },
        'orderbooks_compact': {
            'ttl': '1 HOURS',  # Keep only last hour of json list snapshots
            'partition': 'HOUR',
            'check_sql': "SELECT name FROM tables() WHERE name = 'orderbooks_compact'"
        }
    }
    
    for table_name, config in tables_config.items():
        print(f"\n  Checking table: {table_name}")
        
        # Check if table exists
        result = execute_sql(host, http_port, config['check_sql'])
        
        if result and 'dataset' in result and result['dataset']:
            print(f"    ✅ Table exists")
            
            # Update TTL if specified
            if config['ttl']:
                print(f"    Configuring TTL: {config['ttl']}")
                ttl_sql = f"ALTER TABLE {table_name} SET TTL {config['ttl']}"
                result = execute_sql(host, http_port, ttl_sql)
                
                if result and result.get('ddl') == 'OK':
                    print(f"    ✅ TTL set to {config['ttl']}")
                elif result and 'error' in result:
                    # TTL might already be set or other issue
                    if 'already' in result['error'].lower() or 'same' in result['error'].lower():
                        print(f"    ℹ️  TTL already configured")
                    else:
                        print(f"    ⚠️  TTL warning: {result['error']}")
                else:
                    print(f"    ✅ TTL applied")
        else:
            print(f"    ℹ️  Table does not exist yet (will be auto-created on first write)")
    
    # Create materialized view if it doesn't exist
    print(f"\n  Checking materialized view: orderbooks_latest_1s")
    result = execute_sql(host, http_port, "SELECT view_name FROM materialized_views() WHERE view_name = 'orderbooks_latest_1s'")
    
    if result and 'dataset' in result and result['dataset']:
        print(f"    ✅ Materialized view exists")
    else:
        print(f"    Creating materialized view...")
        mv_sql = """
            CREATE MATERIALIZED VIEW orderbooks_latest_1s AS 
            SELECT 
                timestamp, 
                exchange, 
                symbol, 
                last(bids) AS bids, 
                last(asks) AS asks 
            FROM orderbooks_compact 
            SAMPLE BY 1s
        """
        result = execute_sql(host, http_port, mv_sql)
        
        if result and result.get('ddl') == 'OK':
            print(f"    ✅ Materialized view created")
        elif result and 'error' in result:
            if 'already exists' in result['error'].lower():
                print(f"    ℹ️  Materialized view already exists")
            else:
                print(f"    ❌ Error creating view: {result['error']}")
        else:
            print(f"    ✅ Materialized view created")
    
    print("\n✅ QuestDB initialization complete!\n")
    
    # Print configuration summary
    print("📋 Table Configuration Summary:")
    print(f"  {'Table':<25} {'Partition':<10} {'TTL':<15}")
    print(f"  {'-'*25} {'-'*10} {'-'*15}")
    for table_name, config in tables_config.items():
        ttl_display = config['ttl'] if config['ttl'] else 'None'
        print(f"  {table_name:<25} {config['partition']:<10} {ttl_display:<15}")
    print()


if __name__ == '__main__':
    # Parse command line arguments
    import sys
    
    host = sys.argv[1] if len(sys.argv) > 1 else 'questdb'
    
    init_tables(host=host, wait_for_db=True)
