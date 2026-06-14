"""
migrate_db.py — Add Phase 4 contract columns to existing SQLite database.
Run once: python3 migrate_db.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "arcane.db")

MARKETS_COLUMNS = [
    ("contract_market_id",   "INTEGER"),
    ("contract_address",     "TEXT"),
    ("on_chain_status",      "TEXT DEFAULT 'not deployed'"),
    ("dispute_ends_at",      "TEXT"),
    ("proposed_outcome_int", "INTEGER DEFAULT 0"),
    ("final_outcome_int",    "INTEGER DEFAULT 0"),
    ("escrowed_usdc_raw",    "INTEGER DEFAULT 0"),
    ("on_chain_yes_shares",  "INTEGER DEFAULT 0"),
    ("on_chain_no_shares",   "INTEGER DEFAULT 0"),
    ("evidence_uri",         "TEXT"),
    ("create_market_tx",     "TEXT"),
    ("close_market_tx",      "TEXT"),
    ("resolution_tx",        "TEXT"),
    ("finalize_tx",          "TEXT"),
    ("contract_synced",      "INTEGER DEFAULT 0"),
    # Aliases used by main.py
    ("create_tx_hash",       "TEXT"),
    ("resolve_tx_hash",      "TEXT"),
]

TRADES_COLUMNS = [
    ("on_chain_tx_hash",     "TEXT"),
    ("on_chain_block",       "INTEGER"),
    ("on_chain_shares",      "INTEGER DEFAULT 0"),
    ("x402_payment_id",      "TEXT"),
    ("x402_settled",         "INTEGER DEFAULT 0"),
    ("x402_authorization",   "TEXT"),
]

PAYMENTS_COLUMNS = [
    ("x402_settled",         "INTEGER DEFAULT 0"),
    ("x402_authorization",   "TEXT"),
    ("x402_batch_id",        "TEXT"),
    ("eip3009_nonce",        "TEXT"),
]

def add_columns(conn, table, columns):
    cursor = conn.cursor()
    # Get existing columns
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    added = []
    for col_name, col_type in columns:
        if col_name not in existing:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                added.append(col_name)
                print(f"  + {table}.{col_name} ({col_type})")
            except sqlite3.OperationalError as e:
                print(f"  ! {table}.{col_name}: {e}")
    return added

def create_new_tables(conn):
    cursor = conn.cursor()

    # ContractEvent table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contract_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_db_id TEXT,
        contract_market_id INTEGER,
        event_name TEXT NOT NULL,
        tx_hash TEXT,
        block_number INTEGER,
        log_index INTEGER,
        args_json TEXT,
        arcscan_url TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )
    """)
    print("  ✓ contract_events table")

    # PayoutClaim table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payout_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        market_db_id TEXT,
        contract_market_id INTEGER,
        claimant TEXT NOT NULL,
        claim_type TEXT NOT NULL,
        amount_usdc_raw INTEGER DEFAULT 0,
        amount_usdc REAL DEFAULT 0.0,
        tx_hash TEXT,
        block_number INTEGER,
        arcscan_url TEXT,
        claimed_at TEXT DEFAULT (datetime('now'))
    )
    """)
    print("  ✓ payout_claims table")

    # X402Payment table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS x402_payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payment_id TEXT UNIQUE,
        payer TEXT,
        payee TEXT,
        amount_usdc_raw INTEGER DEFAULT 0,
        amount_usdc REAL DEFAULT 0.0,
        memo TEXT,
        endpoint TEXT,
        x402_settled INTEGER DEFAULT 0,
        x402_authorization TEXT,
        x402_batch_id TEXT,
        eip3009_nonce TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        settled_at TEXT
    )
    """)
    print("  ✓ x402_payments table")

    conn.commit()


def main():
    print(f"Migrating database: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("ERROR: Database not found. Start the server first to create it.")
        return

    conn = sqlite3.connect(DB_PATH)
    try:
        print("\nAdding columns to markets table:")
        add_columns(conn, "markets", MARKETS_COLUMNS)

        print("\nAdding columns to trades table:")
        add_columns(conn, "trades", TRADES_COLUMNS)

        print("\nAdding columns to payments table:")
        add_columns(conn, "payments", PAYMENTS_COLUMNS)

        print("\nCreating new tables:")
        create_new_tables(conn)

        conn.commit()
        print("\nMigration complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
