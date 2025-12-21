import sqlite3

con = sqlite3.connect("database.db")
cur = con.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS students (
    uid TEXT UNIQUE NOT NULL,
    usn TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    balance REAL DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    photo TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT,
    amount REAL,
    status TEXT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS admins (
    username TEXT PRIMARY KEY,
    password_hash TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
""")

con.commit()
con.close()
print("Database setup complete!")


