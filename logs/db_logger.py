import sqlite3
import time
from threading import Lock


class DBLogger:
    """
    Thread-safe SQLite logger used to store all agent-related events
    and microgrid auction results.

    This logger supports:
        - Persisting all agent-level events (status updates, offers,
          requests, CFPs, etc.)
        - Recording completed auction transactions between agents
        - Automatic creation of required tables if they do not exist
        - Safe concurrent writes using a thread-level lock

    Args:
        db_path (str): Path to the SQLite database file.
    """

    def __init__(self, db_path: str = "logs/agents_logs.db"):
        """Initialize the SQLite connection and ensure the schema exists.

        Args:
            db_path (str): Path to the SQLite database used for logging.
        """
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = Lock()
        self._create_tables()

    # INTERNAL SETUP

    def _create_tables(self) -> None:
        """
        Create the required database tables (`events`, `auction_results`)
        if they do not already exist.

        This method is automatically executed during initialization.
        """
        with self.conn:
            # General agent events log
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

            # Finalized auction transaction records
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

    # PUBLIC LOGGING METHODS

    def log_event(
        self,
        kind: str,
        jid: str,
        kw: float = 0.0,
        price: float = 0.0,
        round_id: int | None = None
    ) -> None:
        """
        Log a single agent-level event.

        Typical use cases:
            - Household sending a CFP response
            - Producer reporting status
            - Storage unit receiving a control command
            - Any message exchanged during the auction mechanism

        Args:
            kind (str): Event type identifier (e.g. "status", "offer_sent",
                        "energy_request", "environment_update").
            jid (str): JID of the agent generating the event.
            kw (float): Amount of energy associated with the event.
            price (float): Price linked to the event.
            round_id (int | None): Optional market round identifier.
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
        Record the final result of a completed auction transaction.

        Args:
            round_id (int): Unique market round identifier.
            buyer (str): JID of the buyer agent.
            seller (str): JID of the seller agent.
            kw (float): Energy traded in the transaction (kWh).
            price (float): Clearing price agreed by both parties (€/kWh).

        Returns:
            None
        """
        with self.lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO auction_results (round_id, buyer, seller, kw, price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (round_id, buyer, seller, kw, price, time.time()),
            )
