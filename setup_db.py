import sqlite3

# Create / connect to database
con = sqlite3.connect("database.db")
cur = con.cursor()

# Students table
cur.execute("""
CREATE TABLE IF NOT EXISTS students (
    uid TEXT UNIQUE NOT NULL,        -- RFID UID
    usn TEXT UNIQUE NOT NULL,        -- Student login ID
    name TEXT NOT NULL,
    password TEXT NOT NULL,          -- plain for now
    balance REAL DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    photo TEXT,
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

# Admin table (unchanged for now)
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

