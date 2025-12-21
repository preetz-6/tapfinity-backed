import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set")

con = psycopg2.connect(DATABASE_URL)
cur = con.cursor()

# ----------------------------------------------------------
# STUDENTS TABLE
# ----------------------------------------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS students (
    uid TEXT UNIQUE NOT NULL,
    usn TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    balance NUMERIC DEFAULT 0,
    blocked BOOLEAN DEFAULT FALSE,
    photo TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# ----------------------------------------------------------
# TRANSACTIONS TABLE
# ----------------------------------------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    uid TEXT,
    amount NUMERIC,
    status TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

# ----------------------------------------------------------
# ADMINS TABLE
# ----------------------------------------------------------
cur.execute("""
CREATE TABLE IF NOT EXISTS admins (
    username TEXT PRIMARY KEY,
    password_hash TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

con.commit()
cur.close()
con.close()

print("PostgreSQL database setup complete!")
