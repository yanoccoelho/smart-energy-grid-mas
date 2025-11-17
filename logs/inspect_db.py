import sqlite3
import pandas as pd
from loguru import logger


DB_PATH = "logs/agents_logs.db"


def connect_database(db_path: str = DB_PATH) -> sqlite3.Connection:
    """
    Establish a connection to the SQLite database.

    Args:
        db_path (str): Path to the SQLite database file.

    Returns:
        sqlite3.Connection: Active connection object.
    """
    conn = sqlite3.connect(db_path)
    print(f"Connected to database: {db_path}\n")
    return conn


def list_tables(conn: sqlite3.Connection) -> None:
    """
    Print all available table names inside the selected SQLite database.

    Args:
        conn (sqlite3.Connection): Active database connection.
    """
    tables = pd.read_sql_query(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;", conn
    )
    print("Available tables:")
    print(tables, "\n")


def show_last_events(conn: sqlite3.Connection, limit: int = 20) -> None:
    """
    Display the most recent logged events from the `events` table.

    Args:
        conn (sqlite3.Connection): Active database connection.
        limit (int): Number of recent events to display.
    """
    try:
        df_events = pd.read_sql_query(
            f"""
            SELECT id,
                   datetime(timestamp, 'unixepoch', 'localtime') AS time,
                   kind,
                   jid,
                   kw,
                   price,
                   round_id
            FROM events
            ORDER BY timestamp DESC
            LIMIT {limit};
            """,
            conn,
        )

        print("Last recorded events:")
        print(df_events.to_string(index=False))

    except Exception as e:
        logger.warning(f"Could not read 'events' table: {e}")


def show_last_auction_results(conn: sqlite3.Connection, limit: int = 10) -> None:
    """
    Display the most recent auction results from the `auction_results` table.

    Args:
        conn (sqlite3.Connection): Active database connection.
        limit (int): Number of results to display.
    """
    try:
        df_auctions = pd.read_sql_query(
            f"""
            SELECT id,
                   round_id,
                   buyer,
                   seller,
                   kw,
                   price,
                   datetime(timestamp, 'unixepoch', 'localtime') AS time
            FROM auction_results
            ORDER BY round_id DESC
            LIMIT {limit};
            """,
            conn,
        )

        print("\nLast auction results:")
        print(df_auctions.to_string(index=False))

    except Exception as e:
        logger.warning(f"Could not read 'auction_results' table: {e}")


def main() -> None:
    """
    Main execution workflow:
    - Connect to database
    - List available tables
    - Show last events
    - Show last auction results
    - Close the connection
    """
    conn = connect_database()
    list_tables(conn)
    show_last_events(conn)
    show_last_auction_results(conn)
    conn.close()
    print("\nDatabase connection closed.")


if __name__ == "__main__":
    main()
