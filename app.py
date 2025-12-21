from flask import Flask, request, jsonify, render_template, session, redirect
import os
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from twilio.rest import Client
from functools import wraps
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# ----------------------------------------------------------
# 🔐 SESSION CONFIG
# ----------------------------------------------------------
app.secret_key = os.getenv("SECRET_KEY", "tapfinity-dev-secret")

# ----------------------------------------------------------
# DATABASE
# ----------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )

# ----------------------------------------------------------
# TWILIO
# ----------------------------------------------------------
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM")

twilio_client = Client(TWILIO_SID, TWILIO_TOKEN) if TWILIO_SID else None

def send_whatsapp(phone, msg):
    if not phone or not twilio_client:
        return
    try:
        twilio_client.messages.create(
            from_=TWILIO_FROM,
            to=f"whatsapp:{phone}",
            body=msg
        )
    except Exception as e:
        print("WhatsApp error:", e)

# ----------------------------------------------------------
# SESSION GUARDS
# ----------------------------------------------------------
def admin_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if "admin" not in session:
            return jsonify({"status": "unauthorized"}), 401
        return f(*a, **k)
    return wrapper

def student_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if "student" not in session:
            return jsonify({"status": "unauthorized"}), 401
        return f(*a, **k)
    return wrapper

# ----------------------------------------------------------
# INIT DB
# ----------------------------------------------------------
def init_db():
    con = get_db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS students(
        uid TEXT UNIQUE,
        usn TEXT UNIQUE,
        name TEXT,
        phone TEXT,
        password_hash TEXT,
        balance NUMERIC DEFAULT 0,
        blocked BOOLEAN DEFAULT FALSE
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS transactions(
        id SERIAL PRIMARY KEY,
        uid TEXT,
        amount NUMERIC,
        status TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins(
        username TEXT UNIQUE,
        password_hash TEXT
    )""")

    con.commit()
    con.close()

init_db()

# ----------------------------------------------------------
# HEALTH
# ----------------------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({"ok": True})

# ----------------------------------------------------------
# ADMIN AUTH
# ----------------------------------------------------------
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    d = request.json
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT password_hash FROM admins WHERE username=%s", (d["username"],))
    a = cur.fetchone()
    con.close()

    if not a or not check_password_hash(a["password_hash"], d["password"]):
        return jsonify({"status": "error"}), 403

    session["admin"] = d["username"]
    return jsonify({"status": "success"})

@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"status": "success"})

# ----------------------------------------------------------
# STUDENT AUTH
# ----------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def student_login():
    d = request.json
    usn = d["usn"].upper()

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT password_hash FROM students WHERE usn=%s", (usn,))
    s = cur.fetchone()
    con.close()

    if not s or not check_password_hash(s["password_hash"], d["password"]):
        return jsonify({"status": "error"}), 403

    session["student"] = usn
    return jsonify({"status": "success"})

@app.route("/api/student/logout", methods=["POST"])
def student_logout():
    session.clear()
    return jsonify({"status": "success"})

# ----------------------------------------------------------
# ADMIN ACTIONS
# ----------------------------------------------------------
@app.route("/api/add_student", methods=["POST"])
@admin_required
def add_student():
    d = request.json
    con = get_db()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO students(usn,uid,name,phone,password_hash,balance)
        VALUES(%s,%s,%s,%s,%s,%s)
    """, (
        d["usn"].upper(),
        d["uid"].upper(),
        d["name"],
        d["phone"],
        generate_password_hash(d["password"]),
        d.get("balance",0)
    ))

    con.commit()
    con.close()

    send_whatsapp(d["phone"], f"TapFinity ✅ Account created\nUSN: {d['usn']}")
    return jsonify({"status": "success"})

@app.route("/api/add_balance", methods=["POST"])
@admin_required
def add_balance():
    d = request.json
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT uid,phone,balance FROM students WHERE usn=%s",(d["usn"].upper(),))
    s = cur.fetchone()

    new_bal = s["balance"] + d["amount"]
    cur.execute("UPDATE students SET balance=%s WHERE usn=%s",(new_bal,d["usn"].upper()))
    cur.execute("INSERT INTO transactions(uid,amount,status) VALUES(%s,%s,'topup')",(s["uid"],d["amount"]))

    con.commit()
    con.close()

    send_whatsapp(s["phone"], f"₹{d['amount']} added\nBalance ₹{new_bal}")
    return jsonify({"status": "success"})

@app.route("/api/block_card", methods=["POST"])
@admin_required
def block_card():
    usn = request.json["usn"].upper()
    con = get_db()
    cur = con.cursor()

    cur.execute("UPDATE students SET blocked=TRUE WHERE usn=%s",(usn,))
    cur.execute("SELECT phone FROM students WHERE usn=%s",(usn,))
    phone = cur.fetchone()["phone"]

    con.commit()
    con.close()

    send_whatsapp(phone,"🚫 Card BLOCKED")
    return jsonify({"status":"success"})

@app.route("/api/unblock_card", methods=["POST"])
@admin_required
def unblock_card():
    usn = request.json["usn"].upper()
    con = get_db()
    cur = con.cursor()

    cur.execute("UPDATE students SET blocked=FALSE WHERE usn=%s",(usn,))
    cur.execute("SELECT phone FROM students WHERE usn=%s",(usn,))
    phone = cur.fetchone()["phone"]

    con.commit()
    con.close()

    send_whatsapp(phone,"✅ Card UNBLOCKED")
    return jsonify({"status":"success"})

# ----------------------------------------------------------
# RFID VERIFY + DEDUCT (ANTI-SPAM)
# ----------------------------------------------------------
last_tx = {}

@app.route("/verify")
def verify():
    uid = request.args.get("uid","").upper()
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT name,balance,blocked FROM students WHERE uid=%s",(uid,))
    s = cur.fetchone()
    con.close()

    if not s or s["blocked"]:
        return jsonify({"ok":False}),403

    return jsonify({"ok":True,"name":s["name"],"balance":float(s["balance"])})

@app.route("/deduct", methods=["POST"])
def deduct():
    d = request.json
    uid = d["uid"].upper()
    amt = d["amount"]

    # Anti-repeat (5 sec)
    if uid in last_tx and datetime.now() - last_tx[uid] < timedelta(seconds=5):
        return jsonify({"ok":False})

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM students WHERE uid=%s",(uid,))
    s = cur.fetchone()

    if not s or s["blocked"] or s["balance"] < amt:
        return jsonify({"ok":False})

    new_bal = s["balance"] - amt
    cur.execute("UPDATE students SET balance=%s WHERE uid=%s",(new_bal,uid))
    cur.execute("INSERT INTO transactions(uid,amount,status) VALUES(%s,%s,'success')",(uid,amt))
    con.commit()
    con.close()

    last_tx[uid] = datetime.now()
    send_whatsapp(s["phone"], f"₹{amt} spent\nBalance ₹{new_bal}")
    return jsonify({"ok":True,"balance":float(new_bal)})

# ----------------------------------------------------------
# ANALYTICS
# ----------------------------------------------------------
@app.route("/api/admin/analytics")
@admin_required
def admin_analytics():
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT COUNT(*) c FROM students")
    students = cur.fetchone()["c"]

    cur.execute("SELECT COALESCE(SUM(balance),0) s FROM students")
    bal = cur.fetchone()["s"]

    cur.execute("SELECT COUNT(*) c FROM transactions")
    tx = cur.fetchone()["c"]

    con.close()
    return jsonify({
        "total_students": students,
        "total_balance": float(bal),
        "transactions": tx
    })

@app.route("/api/admin/transactions")
@admin_required
def admin_transactions():
    con = get_db()
    cur = con.cursor()
    cur.execute("""
        SELECT t.amount,t.status,t.timestamp,s.name,s.usn
        FROM transactions t JOIN students s ON s.uid=t.uid
        ORDER BY t.timestamp DESC LIMIT 50
    """)
    rows = cur.fetchall()
    con.close()
    return jsonify(rows)

# ----------------------------------------------------------
# HTML ROUTES
# ----------------------------------------------------------
@app.route("/")
def index(): return render_template("index.html")

@app.route("/admin_login")
def admin_login_page(): return render_template("admin_login.html")

@app.route("/student_login")
def student_login_page(): return render_template("student_login.html")

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

# ----------------------------------------------------------
# RUN
# ----------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
