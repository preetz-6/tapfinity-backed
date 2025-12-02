import sqlite3

# Create / connect to database
con = sqlite3.connect("database.db")
cur = con.cursor()

# Students table (with pin)
cur.execute("""
CREATE TABLE IF NOT EXISTS students (
    uid TEXT PRIMARY KEY,
    name TEXT,
    balance REAL DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    pin TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

# Transactions table
cur.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT,
    amount REAL,
    status TEXT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

# Admin table
cur.execute("""
CREATE TABLE IF NOT EXISTS admins (
    username TEXT PRIMARY KEY,
    password TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")


con.commit()
con.close()

print("Database setup complete!")
