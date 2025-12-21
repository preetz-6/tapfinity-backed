from flask import Flask, request, jsonify, render_template
import os
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import psycopg2
import psycopg2.extras
from twilio.rest import Client
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ----------------------------------------------------------
# FILE UPLOAD SETTINGS
# ----------------------------------------------------------
UPLOAD_FOLDER = "static/photos"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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
# TWILIO (WhatsApp)
# ----------------------------------------------------------
TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM")  # whatsapp:+14155238886

twilio_client = None
if TWILIO_SID and TWILIO_TOKEN:
    twilio_client = Client(TWILIO_SID, TWILIO_TOKEN)

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
# INIT DB (safe on Render)
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
    balance = d.get("balance", 0)

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
        return jsonify({"status":"error","message":"USN or UID exists"}), 400
    finally:
        con.close()

    send_whatsapp(phone, f"TapFinity ✅\nAccount created\nUSN: {usn}")

    return jsonify({"status":"success"})


# ----------------------------------------------------------
# STUDENT LOGIN
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
        return jsonify({"status":"error","message":"Invalid credentials"}), 403

    return jsonify({"status":"success"})

# ----------------------------------------------------------
# ADMIN LOGIN (NO SESSION – BASIC)
# ----------------------------------------------------------
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.json or {}

    username = (data.get("username") or "").strip()
    password = data.get("password")

    if not username or not password:
        return jsonify({
            "status": "error",
            "message": "Username and password required"
        }), 400

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT password_hash FROM admins WHERE username=%s",
        (username,)
    )
    admin = cur.fetchone()
    con.close()

    if not admin or not check_password_hash(admin["password_hash"], password):
        return jsonify({
            "status": "error",
            "message": "Invalid admin credentials"
        }), 403

    return jsonify({"status": "success"})

# ----------------------------------------------------------
# CHANGE PASSWORD
# ----------------------------------------------------------
@app.route("/api/student/change_password", methods=["POST"])
def change_password():
    d = request.json or {}
    usn = d.get("usn","").upper()
    old = d.get("old_password")
    new = d.get("new_password")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT password_hash, phone FROM students WHERE usn=%s", (usn,))
    s = cur.fetchone()

    if not s or not check_password_hash(s["password_hash"], old):
        con.close()
        return jsonify({"status":"error","message":"Incorrect password"}), 403

    cur.execute(
        "UPDATE students SET password_hash=%s WHERE usn=%s",
        (generate_password_hash(new), usn)
    )
    con.commit()
    con.close()

    send_whatsapp(s["phone"], "TapFinity 🔐\nPassword changed successfully")

    return jsonify({"status":"success"})


# ----------------------------------------------------------
# STUDENT DASHBOARD (USN)
# ----------------------------------------------------------
@app.route("/api/student/by_usn/<usn>")
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
    return jsonify({"status":"success","student":s,"transactions":tx})


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
        return jsonify({"ok":False}),404
    if s["blocked"]:
        return jsonify({"ok":False,"error":"blocked"}),403

    return jsonify({"ok":True,"name":s["name"],"balance":float(s["balance"])})


# ----------------------------------------------------------
# RFID DEDUCT
# ----------------------------------------------------------
@app.route("/deduct", methods=["POST"])
def deduct():
    d = request.json or {}
    uid = d.get("uid","").upper()
    amt = d.get("amount")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM students WHERE uid=%s",(uid,))
    s = cur.fetchone()

    if not s or s["blocked"] or s["balance"] < amt:
        if s:
            cur.execute(
                "INSERT INTO transactions(uid,amount,status) VALUES(%s,%s,%s)",
                (uid, amt, "failed")
            )
            con.commit()
            send_whatsapp(
                s["phone"],
                "TapFinity ❌\nTransaction failed\nInsufficient balance"
            )
        con.close()
        return jsonify({"ok":False}),400

    new_bal = s["balance"] - amt
    cur.execute("UPDATE students SET balance=%s WHERE uid=%s",(new_bal,uid))
    cur.execute(
        "INSERT INTO transactions(uid,amount,status) VALUES(%s,%s,%s)",
        (uid, amt, "success")
    )
    con.commit()
    con.close()

    send_whatsapp(
        s["phone"],
        f"TapFinity 💸\n₹{amt} spent\nBalance: ₹{new_bal}"
    )

    return jsonify({"ok":True,"balance":float(new_bal)})


# ----------------------------------------------------------
# ADD BALANCE (ADMIN)
# ----------------------------------------------------------
@app.route("/api/add_balance", methods=["POST"])
def add_balance():
    d = request.json or {}
    usn = d.get("usn","").upper()
    amt = d.get("amount")

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT uid,phone,balance FROM students WHERE usn=%s",(usn,))
    s = cur.fetchone()

    new_bal = s["balance"] + amt
    cur.execute("UPDATE students SET balance=%s WHERE usn=%s",(new_bal,usn))
    cur.execute(
        "INSERT INTO transactions(uid,amount,status) VALUES(%s,%s,%s)",
        (s["uid"], amt, "topup")
    )
    con.commit()
    con.close()

    send_whatsapp(
        s["phone"],
        f"TapFinity 💰\n₹{amt} added\nNew balance: ₹{new_bal}"
    )

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

    send_whatsapp(phone, "TapFinity 🚫\nYour card has been BLOCKED")

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

    send_whatsapp(phone, "TapFinity ✅\nYour card has been UNBLOCKED")

    return jsonify({"status":"success"})

# ----------------------------------------------------------
# ADMIN ANALYTICS
# ----------------------------------------------------------
@app.route("/api/admin/analytics")
def admin_analytics():
    con = get_db()
    cur = con.cursor()

    # Total transactions
    cur.execute("SELECT COUNT(*) AS count FROM transactions")
    total_tx = cur.fetchone()["count"]

    # Total spent (successful deductions)
    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) AS spent
        FROM transactions
        WHERE status = 'success'
    """)
    total_spent = cur.fetchone()["spent"]

    # Total topups
    cur.execute("""
        SELECT COALESCE(SUM(amount), 0) AS topup
        FROM transactions
        WHERE status = 'topup'
    """)
    total_topups = cur.fetchone()["topup"]

    # Blocked cards
    cur.execute("SELECT COUNT(*) AS blocked FROM students WHERE blocked = TRUE")
    blocked_cards = cur.fetchone()["blocked"]

    # Active cards
    cur.execute("SELECT COUNT(*) AS active FROM students WHERE blocked = FALSE")
    active_cards = cur.fetchone()["active"]

    con.close()

    return jsonify({
        "status": "success",
        "metrics": {
            "total_transactions": total_tx,
            "total_spent": float(total_spent),
            "total_topups": float(total_topups),
            "blocked_cards": blocked_cards,
            "active_cards": active_cards
        }
    })

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
    blocked_students = cur.fetchone()["blocked"]

    cur.execute("SELECT COALESCE(SUM(balance),0) AS total_balance FROM students")
    total_balance = cur.fetchone()["total_balance"]

    cur.execute("""
        SELECT COALESCE(SUM(amount),0) AS total_spent
        FROM transactions
        WHERE status='success'
    """)
    total_spent = cur.fetchone()["total_spent"]

    cur.execute("SELECT COUNT(*) AS total_tx FROM transactions")
    total_tx = cur.fetchone()["total_tx"]

    con.close()

    return jsonify({
        "status": "success",
        "analytics": {
            "total_students": total_students,
            "blocked_students": blocked_students,
            "total_balance": float(total_balance),
            "total_spent": float(total_spent),
            "total_transactions": total_tx
        }
    })

# ----------------------------------------------------------
# ADMIN TRANSACTIONS LIST
# ----------------------------------------------------------
@app.route("/api/admin/transactions")
def admin_transactions():
    limit = int(request.args.get("limit", 50))

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        SELECT
            t.amount,
            t.status,
            t.timestamp,
            s.name,
            s.usn
        FROM transactions t
        JOIN students s ON s.uid = t.uid
        ORDER BY t.timestamp DESC
        LIMIT %s
    """, (limit,))

    tx = cur.fetchall()
    con.close()

    return jsonify({
        "status": "success",
        "transactions": tx
    })

# ----------------------------------------------------------
# HTML ROUTES
# ----------------------------------------------------------
@app.route("/")
def index(): return render_template("index.html")

@app.route("/student_login")
def student_login_page(): return render_template("student_login.html")

@app.route("/student")
def student_page(): return render_template("student.html")

@app.route("/admin")
def admin_page(): return render_template("admin.html")


# ----------------------------------------------------------
# RUN
# ----------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
