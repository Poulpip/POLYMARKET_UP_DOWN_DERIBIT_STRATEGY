import sqlite3
import logging
from datetime import datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = "paper_trades.db"

def init_db():
    """Initialize the SQLite database with the paper trades schema."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id TEXT,
                market_title TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_polymarket_price REAL NOT NULL,
                entry_model_prob REAL NOT NULL,
                size_usdc REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                exit_price REAL,
                realized_pnl REAL,
                tx_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP,
                peak_price REAL,
                exit_reason TEXT
            )
        ''')
        try:
            cursor.execute("ALTER TABLE paper_trades ADD COLUMN token_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE paper_trades ADD COLUMN tx_hash TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE paper_trades ADD COLUMN peak_price REAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE paper_trades ADD COLUMN exit_reason TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE paper_trades ADD COLUMN barrier REAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE paper_trades ADD COLUMN expiry_timestamp TEXT")
        except sqlite3.OperationalError:
            pass
        # Ensure we don't buy the same direction for the same market title twice while open
        # We don't strictly enforce unique constraint in DB, we'll handle in logic, 
        # but an index on market_title is good for querying.
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_market_title 
            ON paper_trades(market_title)
        ''')
        conn.commit()
    logger.info("Database initialized successfully.")

@contextmanager
def get_db_connection():
    """Context manager for SQLite connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def record_paper_trade(market_title: str, direction: str, entry_price: float, model_prob: float, size_usdc: float, token_id: str = None, tx_hash: str = None, peak_price: float = None, barrier: float = None, expiry_timestamp: str = None) -> int:
    """Record a new paper or live trade."""
    if peak_price is None:
        peak_price = entry_price
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO paper_trades (
                market_title, direction, entry_polymarket_price, 
                entry_model_prob, size_usdc, status, created_at, token_id, tx_hash, peak_price, barrier, expiry_timestamp
            ) VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?)
        ''', (market_title, direction, entry_price, model_prob, size_usdc, datetime.utcnow(), token_id, tx_hash, peak_price, barrier, expiry_timestamp))
        conn.commit()
        return cursor.lastrowid

def update_peak_price(trade_id: int, peak_price: float):
    """Update the peak price observed for an open trade."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE paper_trades
            SET peak_price = ?
            WHERE id = ?
        ''', (peak_price, trade_id))
        conn.commit()

def get_open_trades() -> list:
    """Get all currently open paper trades."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM paper_trades WHERE status = 'OPEN'")
        return [dict(row) for row in cursor.fetchall()]

def close_paper_trade(trade_id: int, exit_price: float, realized_pnl: float, exit_tx_hash: str = None, exit_reason: str = None):
    """Close an open paper trade."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE paper_trades 
            SET status = 'CLOSED', exit_price = ?, realized_pnl = ?, closed_at = ?, tx_hash = COALESCE(?, tx_hash), exit_reason = ?
            WHERE id = ?
        ''', (exit_price, realized_pnl, datetime.utcnow(), exit_tx_hash, exit_reason, trade_id))
        conn.commit()

def get_all_trades() -> list:
    """Get all trades for the dashboard."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM paper_trades ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]
