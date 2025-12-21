from flask import Flask, request, jsonify, render_template
import os
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from twilio.rest import Client

app = Flask(__name__)
CORS(app)

# ----------------------------------------------------------
# DATABASE
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
# ADMIN LOGIN
# ----------------------------------------------------------
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    d = request.json or {}
    username = d.get("username")
    password = d.get("password")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT password_hash FROM admins WHERE username=%s", (username,))
    a = cur.fetchone()
    con.close()

    if not a or not check_password_hash(a["password_hash"], password):
        return jsonify({"status": "error"}), 403

    return jsonify({"status": "success"})

# ----------------------------------------------------------
# STUDENT LOGIN
# ----------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def student_login():
    d = request.json or {}
    usn = d.get("usn", "").upper()
    password = d.get("password")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT password_hash FROM students WHERE usn=%s", (usn,))
    s = cur.fetchone()
    con.close()

    if not s or not check_password_hash(s["password_hash"], password):
        return jsonify({"status": "error"}), 403

    return jsonify({"status": "success", "usn": usn})

# ----------------------------------------------------------
# ADD STUDENT (ADMIN)
# ----------------------------------------------------------
@app.route("/api/add_student", methods=["POST"])
def add_student():
    d = request.json or {}

    usn = d.get("usn","").upper()
    uid = d.get("uid","").upper()
    name = d.get("name")
    phone = d.get("phone")
    password = d.get("password")
    balance = float(d.get("balance", 0))

    if not all([usn, uid, name, phone, password]):
        return jsonify({"status":"error","message":"All fields required"}), 400

    pwd_hash = generate_password_hash(password)

    con = get_db()
    cur = con.cursor()
    try:
        cur.execute("""
            INSERT INTO students (usn, uid, name, phone, password_hash, balance)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (usn, uid, name, phone, pwd_hash, balance))
        con.commit()
    except psycopg2.errors.UniqueViolation:
        con.rollback()
        con.close()
        return jsonify({"status":"error","message":"USN or UID exists"}), 400

    con.close()
    send_whatsapp(phone, f"TapFinity ✅\nAccount created\nUSN: {usn}")
    return jsonify({"status":"success"})

# ----------------------------------------------------------
# STUDENT DASHBOARD + TRANSACTIONS
# ----------------------------------------------------------
@app.route("/api/student/by_usn/<usn>")
def student_by_usn(usn):
    usn = usn.upper()
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT uid, usn, name, balance, blocked, COALESCE(photo,'') AS photo
        FROM students
        WHERE usn=%s
    """, (usn,))
    s = cur.fetchone()

    if not s:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404

    cur.execute("""
        SELECT amount, status, timestamp
        FROM transactions
        WHERE uid=%s
        ORDER BY timestamp DESC
        LIMIT 20
    """, (s["uid"],))
    tx = cur.fetchall()

    cur.execute("""
        SELECT COALESCE(SUM(amount),0) AS spent
        FROM transactions
        WHERE uid=%s AND status='success'
    """, (s["uid"],))
    spent = cur.fetchone()["spent"]

    con.close()

    return jsonify({
        "status": "success",
        "student": {
            "name": s["name"],
            "balance": float(s["balance"]),
            "blocked": s["blocked"],
            "photo": s["photo"]   # ✅ real photo field
        },
        "total_spent": float(spent),
        "tx_count": len(tx),
        "transactions": tx
    })

# ----------------------------------------------------------
# CHANGE PASSWORD (STUDENT)
# ----------------------------------------------------------
@app.route("/api/student/change_password", methods=["POST"])
def change_password():
    d = request.json or {}
    usn = d.get("usn","").upper()
    old = d.get("old_password")
    new = d.get("new_password")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT password_hash,phone FROM students WHERE usn=%s",(usn,))
    s = cur.fetchone()

    if not s or not check_password_hash(s["password_hash"], old):
        con.close()
        return jsonify({"status":"error"}),403

    cur.execute(
        "UPDATE students SET password_hash=%s WHERE usn=%s",
        (generate_password_hash(new),usn)
    )
    con.commit()
    con.close()

    send_whatsapp(s["phone"],"TapFinity 🔐\nPassword changed successfully")
    return jsonify({"status":"success"})

# ----------------------------------------------------------
# RFID VERIFY (ESP32)
# ----------------------------------------------------------
@app.route("/verify")
def verify():
    uid = request.args.get("uid","").upper()
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT name,balance,blocked FROM students WHERE uid=%s",(uid,))
    s = cur.fetchone()
    con.close()

    if not s:
        return jsonify({"ok":False,"error":"not_found"}),404
    if s["blocked"]:
        return jsonify({"ok":False,"error":"blocked"}),403

    return jsonify({"ok":True,"name":s["name"],"balance":float(s["balance"])})

# ----------------------------------------------------------
# RFID DEDUCT (ESP32)
# ----------------------------------------------------------
@app.route("/deduct", methods=["POST"])
def deduct():
    d = request.json or {}
    uid = d.get("uid","").upper()
    amt = float(d.get("amount", 0))

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT balance, blocked, phone FROM students WHERE uid=%s", (uid,))
    s = cur.fetchone()

    if not s or s["blocked"] or float(s["balance"]) < amt:
        if s:
            cur.execute(
                "INSERT INTO transactions(uid, amount, status) VALUES(%s, %s, 'failed')",
                (uid, amt)
            )
            con.commit()
        con.close()
        return jsonify({"ok": False}), 400

    balance = float(s["balance"])   # ✅ FIX
    new_bal = balance - amt          # ✅ FIX

    cur.execute("UPDATE students SET balance=%s WHERE uid=%s", (new_bal, uid))
    cur.execute(
        "INSERT INTO transactions(uid, amount, status) VALUES(%s, %s, 'success')",
        (uid, amt)
    )

    con.commit()
    con.close()

    send_whatsapp(
        s["phone"],
        f"TapFinity 💸\n₹{amt} spent\nBalance: ₹{new_bal}"
    )

    return jsonify({"ok": True, "balance": new_bal})

# ----------------------------------------------------------
# ADD BALANCE (ADMIN)
# ----------------------------------------------------------
@app.route("/api/add_balance", methods=["POST"])
def add_balance():
    d = request.json or {}
    usn = d.get("usn","").upper()
    amt = float(d.get("amount",0))

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT uid,balance,phone FROM students WHERE usn=%s",(usn,))
    s = cur.fetchone()

    new_bal = s["balance"] + amt
    cur.execute("UPDATE students SET balance=%s WHERE usn=%s",(new_bal,usn))
    cur.execute(
        "INSERT INTO transactions(uid,amount,status) VALUES(%s,%s,'topup')",
        (s["uid"],amt)
    )
    con.commit()
    con.close()

    send_whatsapp(s["phone"],f"TapFinity 💰\n₹{amt} added\nBalance: ₹{new_bal}")
    return jsonify({"status":"success"})

# ----------------------------------------------------------
# BLOCK / UNBLOCK
# ----------------------------------------------------------
@app.route("/api/block_card", methods=["POST"])
def block():
    usn = request.json.get("usn","").upper()
    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE students SET blocked=TRUE WHERE usn=%s",(usn,))
    cur.execute("SELECT phone FROM students WHERE usn=%s",(usn,))
    phone = cur.fetchone()["phone"]
    con.commit()
    con.close()

    send_whatsapp(phone,"TapFinity 🚫\nYour card has been BLOCKED")
    return jsonify({"status":"success"})

@app.route("/api/unblock_card", methods=["POST"])
def unblock():
    usn = request.json.get("usn","").upper()
    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE students SET blocked=FALSE WHERE usn=%s",(usn,))
    cur.execute("SELECT phone FROM students WHERE usn=%s",(usn,))
    phone = cur.fetchone()["phone"]
    con.commit()
    con.close()

    send_whatsapp(phone,"TapFinity ✅\nYour card has been UNBLOCKED")
    return jsonify({"status":"success"})

# ----------------------------------------------------------
# ADMIN ANALYTICS
# ----------------------------------------------------------
@app.route("/api/admin/analytics")
def admin_analytics():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) AS total FROM students")
    total_students = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS blocked FROM students WHERE blocked=TRUE")
    blocked = cur.fetchone()["blocked"]

    cur.execute("SELECT COALESCE(SUM(balance),0) AS balance FROM students")
    balance = cur.fetchone()["balance"]

    cur.execute("SELECT COUNT(*) AS tx FROM transactions")
    tx = cur.fetchone()["tx"]

    cur.execute("""
        SELECT COALESCE(SUM(amount),0) AS spent
        FROM transactions WHERE status='success'
    """)
    spent = cur.fetchone()["spent"]

    con.close()

    return jsonify({
        "total_students": total_students,
        "blocked_students": blocked,
        "total_balance": float(balance),
        "total_transactions": tx,
        "total_spent": float(spent)
    })

# ----------------------------------------------------------
# ADMIN TRANSACTIONS
# ----------------------------------------------------------
@app.route("/api/admin/transactions")
def admin_transactions():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT t.amount,t.status,t.timestamp,s.name,s.usn
        FROM transactions t
        JOIN students s ON s.uid=t.uid
        ORDER BY t.timestamp DESC
        LIMIT 50
    """)

    rows = cur.fetchall()
    con.close()
    return jsonify({"transactions": rows})

# ----------------------------------------------------------
# HTML ROUTES
# ----------------------------------------------------------
@app.route("/")
def index(): return render_template("index.html")

@app.route("/student_login")
def student_login_page(): return render_template("student_login.html")

@app.route("/student")
def student_page(): return render_template("student.html")

@app.route("/admin_login")
def admin_login_page(): return render_template("admin_login.html")

@app.route("/admin")
def admin_page(): return render_template("admin.html")

# ----------------------------------------------------------
# RUN
# ----------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
