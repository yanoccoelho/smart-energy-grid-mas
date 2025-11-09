import sqlite3
import time
from threading import Lock


class DBLogger:
    """
    Thread-safe database logger for agent events and auction results.

    This class handles:
    - Storing all agent-level events (status, bids, offers, etc.)
    - Logging completed auction results (buyer, seller, energy traded)
    - Automatic creation of SQLite tables if they do not exist
    """

    def __init__(self, db_path: str = "logs/agents_logs.db"):
        """Initialize the database connection and ensure tables exist."""
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = Lock()
        self._create_tables()

    # Internal setup

    def _create_tables(self) -> None:
        """Create the required tables if they don't exist."""
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    kind TEXT,
                    jid TEXT,
                    kw REAL,
                    price REAL,
                    round_id INTEGER
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS auction_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id INTEGER,
                    buyer TEXT,
                    seller TEXT,
                    kw REAL,
                    price REAL,
                    timestamp REAL
                )
            """)

    # Public logging methods

    def log_event(
        self,
        kind: str,
        jid: str,
        kw: float = 0.0,
        price: float = 0.0,
        round_id: int | None = None
    ) -> None:
        """
        Log a single agent event (status, CFP, offer, request, etc.).

        Args:
            kind: Type of event (e.g., "status", "offer_sent", "cfp_received").
            jid: Agent identifier (JID).
            kw: Energy quantity involved (kW).
            price: Price associated with the event.
            round_id: Optional market round identifier.
        """
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO events (timestamp, kind, jid, kw, price, round_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (time.time(), kind, jid, kw, price, round_id),
            )

    def log_auction(
        self,
        round_id: int,
        buyer: str,
        seller: str,
        kw: float,
        price: float
    ) -> None:
        """
        Log the result of a completed auction transaction.

        Args:
            round_id: Market round identifier.
            buyer: JID of the buyer agent.
            seller: JID of the seller agent.
            kw: Energy traded (kW).
            price: Agreed price per kWh.
        """
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO auction_results (round_id, buyer, seller, kw, price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (round_id, buyer, seller, kw, price, time.time()),
            )
