import sqlite3
import pandas as pd
from loguru import logger

# Connect to the agents database
DB_PATH = "logs/agents_logs.db"
conn = sqlite3.connect(DB_PATH)

print(f"Connected to database: {DB_PATH}\n")

# List all available tables
tables = pd.read_sql_query(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;", conn
)
print("Available tables:")
print(tables, "\n")

# Show last events
try:
    df_events = pd.read_sql_query(
        """
        SELECT id, datetime(timestamp, 'unixepoch', 'localtime') AS time,
               kind, jid, kw, price, round_id
        FROM events
        ORDER BY timestamp DESC
        LIMIT 20;
        """,
        conn,
    )
    print("Last recorded events:")
    print(df_events.to_string(index=False))
except Exception as e:
    logger.warning(f"Could not read 'events' table: {e}")

# Show last auction results
try:
    df_auctions = pd.read_sql_query(
        """
        SELECT id, round_id, buyer, seller, kw, price,
               datetime(timestamp, 'unixepoch', 'localtime') AS time
        FROM auction_results
        ORDER BY round_id DESC
        LIMIT 10;
        """,
        conn,
    )
    print("\nLast auction results:")
    print(df_auctions.to_string(index=False))
except Exception as e:
    logger.warning(f"Could not read 'auction_results' table: {e}")

conn.close()
print("\nDatabase connection closed.")
