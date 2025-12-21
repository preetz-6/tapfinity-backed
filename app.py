from flask import Flask, request, jsonify, render_template, session, redirect
import os
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from twilio.rest import Client
from functools import wraps

app = Flask(__name__)
CORS(app)

# ----------------------------------------------------------
# 🔐 SESSION CONFIG
# ----------------------------------------------------------
app.secret_key = os.getenv("SECRET_KEY", "tapfinity-dev-secret")

# ----------------------------------------------------------
# FILE UPLOAD SETTINGS
# ----------------------------------------------------------
UPLOAD_FOLDER = "static/photos"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# ----------------------------------------------------------
# DATABASE (PostgreSQL)
# ----------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )

# ----------------------------------------------------------
# 🔐 SESSION GUARDS
# ----------------------------------------------------------
def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "admin" not in session:
            return jsonify({"status": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

def student_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "student" not in session:
            return jsonify({"status": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

# ----------------------------------------------------------
# TWILIO (WhatsApp)
# ----------------------------------------------------------
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM")

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN) if TWILIO_SID else None

def send_whatsapp(phone, message):
    if not phone or not twilio_client:
        return
    try:
        twilio_client.messages.create(
            from_=TWILIO_FROM,
            to=f"whatsapp:{phone}",
            body=message
        )
    except Exception as e:
        print("WhatsApp error:", e)

# ----------------------------------------------------------
# INIT DB
# ----------------------------------------------------------
def init_db():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS students (
        uid TEXT UNIQUE NOT NULL,
        usn TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        phone TEXT,
        password_hash TEXT NOT NULL,
        balance NUMERIC DEFAULT 0,
        blocked BOOLEAN DEFAULT FALSE,
        photo TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id SERIAL PRIMARY KEY,
        uid TEXT,
        amount NUMERIC,
        status TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL
    )
    """)

    con.commit()
    con.close()

init_db()

# ----------------------------------------------------------
# HEALTH
# ----------------------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

# ----------------------------------------------------------
# ADMIN LOGIN + SESSION
# ----------------------------------------------------------
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.json or {}
    username = data.get("username")
    password = data.get("password")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT password_hash FROM admins WHERE username=%s", (username,))
    admin = cur.fetchone()
    con.close()

    if not admin or not check_password_hash(admin["password_hash"], password):
        return jsonify({"status": "error"}), 403

    session["admin"] = username
    return jsonify({"status": "success"})

@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin", None)
    return jsonify({"status": "success"})

# ----------------------------------------------------------
# STUDENT LOGIN + SESSION
# ----------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def student_login():
    d = request.json or {}
    usn = d.get("usn","").upper()
    password = d.get("password")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT password_hash FROM students WHERE usn=%s", (usn,))
    s = cur.fetchone()
    con.close()

    if not s or not check_password_hash(s["password_hash"], password):
        return jsonify({"status":"error"}), 403

    session["student"] = usn
    return jsonify({"status":"success"})

@app.route("/api/student/logout", methods=["POST"])
def student_logout():
    session.pop("student", None)
    return jsonify({"status":"success"})

# ----------------------------------------------------------
# STUDENT DASHBOARD
# ----------------------------------------------------------
@app.route("/api/student/by_usn/<usn>")
@student_required
def student_by_usn(usn):
    usn = usn.upper()
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM students WHERE usn=%s", (usn,))
    s = cur.fetchone()

    cur.execute("""
        SELECT amount,status,timestamp
        FROM transactions WHERE uid=%s
        ORDER BY timestamp DESC LIMIT 20
    """, (s["uid"],))

    tx = cur.fetchall()
    con.close()

    return jsonify({"student": s, "transactions": tx})

# ----------------------------------------------------------
# ADMIN ANALYTICS (PROTECTED)
# ----------------------------------------------------------
@app.route("/api/admin/analytics")
@admin_required
def admin_analytics():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) AS total FROM students")
    total_students = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS blocked FROM students WHERE blocked=TRUE")
    blocked_students = cur.fetchone()["blocked"]

    cur.execute("SELECT COALESCE(SUM(balance),0) AS balance FROM students")
    total_balance = cur.fetchone()["balance"]

    cur.execute("SELECT COUNT(*) AS tx FROM transactions")
    total_tx = cur.fetchone()["tx"]

    cur.execute("""
        SELECT COALESCE(SUM(amount),0) AS spent
        FROM transactions WHERE status='success'
    """)
    total_spent = cur.fetchone()["spent"]

    con.close()

    return jsonify({
        "total_students": total_students,
        "blocked_students": blocked_students,
        "total_balance": float(total_balance),
        "total_transactions": total_tx,
        "total_spent": float(total_spent)
    })

# ----------------------------------------------------------
# ADMIN TRANSACTIONS (PROTECTED)
# ----------------------------------------------------------
@app.route("/api/admin/transactions")
@admin_required
def admin_transactions():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT t.amount,t.status,t.timestamp,s.name,s.usn
        FROM transactions t
        JOIN students s ON s.uid=t.uid
        ORDER BY t.timestamp DESC LIMIT 50
    """)

    rows = cur.fetchall()
    con.close()

    return jsonify(rows)

# ----------------------------------------------------------
# HTML ROUTES (SESSION PROTECTED)
# ----------------------------------------------------------
@app.route("/admin")
def admin_page():
    if "admin" not in session:
        return redirect("/admin_login")
    return render_template("admin.html")

@app.route("/student")
def student_page():
    if "student" not in session:
        return redirect("/student_login")
    return render_template("student.html")

@app.route("/admin_login")
def admin_login_page():
    return render_template("admin_login.html")

@app.route("/student_login")
def student_login_page():
    return render_template("student_login.html")

@app.route("/")
def index():
    return render_template("index.html")

# ----------------------------------------------------------
# RUN
# ----------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
