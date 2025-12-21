from flask import Flask, request, jsonify, render_template
import os
from flask_cors import CORS
from werkzeug.utils import secure_filename
import psycopg2
import psycopg2.extras

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
    raise RuntimeError("DATABASE_URL environment variable not set")

def get_db():
    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor
    )


# ----------------------------------------------------------
# HEALTH CHECK
# ----------------------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


# ----------------------------------------------------------
# ADD STUDENT (ADMIN) — USN + UID + PASSWORD
# ----------------------------------------------------------
@app.route("/api/add_student", methods=["POST"])
def add_student():
    data = request.json or {}

    usn = (data.get("usn") or "").strip().upper()
    uid = (data.get("uid") or "").strip().upper()
    name = data.get("name")
    password_hash = data.get("password_hash") or data.get("password")
    balance = data.get("balance", 0)

    if not usn or not uid or not name or not password_hash:
        return jsonify({
            "status": "error",
            "message": "USN, UID, Name and Password required"
        }), 400

    con = get_db()
    cur = con.cursor()

    try:
        cur.execute("""
            INSERT INTO students (usn, uid, name, password_hash, balance)
            VALUES (%s, %s, %s, %s, %s)
        """, (usn, uid, name, password_hash, balance))
        con.commit()
    except psycopg2.errors.UniqueViolation:
        con.rollback()
        con.close()
        return jsonify({
            "status": "error",
            "message": "USN or UID already exists"
        }), 400

    con.close()
    return jsonify({"status": "success"})


# ----------------------------------------------------------
# STUDENT LOGIN — USN + PASSWORD
# ----------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def student_login_api():
    data = request.json or {}

    usn = (data.get("usn") or "").strip().upper()
    password_hash = data.get("password_hash") or data.get("password")

    if not usn or not password_hash:
        return jsonify({
            "status": "error",
            "message": "USN and password required"
        }), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM students WHERE usn=%s", (usn,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404

    if s["password_hash"] != password_hash:
        con.close()
        return jsonify({"status": "error", "message": "Incorrect password"}), 403

    con.close()
    return jsonify({"status": "success"})


# ----------------------------------------------------------
# STUDENT INFO (ADMIN / INTERNAL) — UID BASED
# ----------------------------------------------------------
@app.route("/api/student/<uid>")
def student_info(uid):
    uid = uid.strip().upper()
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM students WHERE uid=%s", (uid,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404

    cur.execute("""
        SELECT * FROM transactions
        WHERE uid=%s
        ORDER BY id DESC LIMIT 5
    """, (uid,))
    tx = cur.fetchall()

    con.close()
    return jsonify({
        "status": "success",
        "student": s,
        "transactions": tx
    })


# ----------------------------------------------------------
# RFID VERIFY (ESP32) — UID ONLY
# ----------------------------------------------------------
@app.route("/verify", methods=["GET"])
def verify_card():
    uid = (request.args.get("uid") or "").strip().upper()

    con = get_db()
    cur = con.cursor()
    cur.execute(
        "SELECT name, balance, blocked FROM students WHERE uid=%s",
        (uid,)
    )
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"ok": False, "error": "User not found"}), 404

    if s["blocked"]:
        con.close()
        return jsonify({"ok": False, "error": "Card blocked"}), 403

    con.close()
    return jsonify({
        "ok": True,
        "name": s["name"],
        "balance": float(s["balance"])
    })


# ----------------------------------------------------------
# RFID DEDUCT (ESP32) — UID ONLY
# ----------------------------------------------------------
@app.route("/deduct", methods=["POST"])
def deduct_amount():
    data = request.json or {}
    uid = (data.get("uid") or "").strip().upper()
    amount = data.get("amount")

    if amount is None:
        return jsonify({"ok": False, "error": "Amount required"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM students WHERE uid=%s", (uid,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"ok": False, "error": "User not found"}), 404

    if s["blocked"]:
        con.close()
        return jsonify({"ok": False, "error": "Card blocked"}), 403

    if s["balance"] < amount:
        cur.execute("""
            INSERT INTO transactions (uid, amount, status)
            VALUES (%s, %s, %s)
        """, (uid, amount, "failed_insufficient"))
        con.commit()
        con.close()
        return jsonify({"ok": False, "error": "Insufficient balance"}), 400

    new_balance = s["balance"] - amount

    cur.execute("UPDATE students SET balance=%s WHERE uid=%s", (new_balance, uid))
    cur.execute("""
        INSERT INTO transactions (uid, amount, status)
        VALUES (%s, %s, %s)
    """, (uid, amount, "success"))

    con.commit()
    con.close()

    return jsonify({"ok": True, "balance": float(new_balance)})


# ----------------------------------------------------------
# ADD BALANCE (ADMIN) — USN BASED
# ----------------------------------------------------------
@app.route("/api/add_balance", methods=["POST"])
def add_balance():
    data = request.json or {}

    usn = (data.get("usn") or "").strip().upper()
    amount = data.get("amount")

    if not usn or amount is None:
        return jsonify({"status": "error", "message": "USN and amount required"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM students WHERE usn=%s", (usn,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404

    new_balance = s["balance"] + amount

    cur.execute("UPDATE students SET balance=%s WHERE usn=%s", (new_balance, usn))
    cur.execute("""
        INSERT INTO transactions (uid, amount, status)
        VALUES (%s, %s, %s)
    """, (s["uid"], amount, "topup"))

    con.commit()
    con.close()

    return jsonify({"status": "success", "new_balance": float(new_balance)})


# ----------------------------------------------------------
# BLOCK / UNBLOCK (ADMIN) — USN BASED
# ----------------------------------------------------------
@app.route("/api/block_card", methods=["POST"])
def block_card():
    usn = (request.json.get("usn") or "").strip().upper()

    if not usn:
        return jsonify({"status": "error", "message": "USN required"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE students SET blocked=TRUE WHERE usn=%s", (usn,))
    con.commit()
    con.close()

    return jsonify({"status": "success"})


@app.route("/api/unblock_card", methods=["POST"])
def unblock_card():
    usn = (request.json.get("usn") or "").strip().upper()

    if not usn:
        return jsonify({"status": "error", "message": "USN required"}), 400

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE students SET blocked=FALSE WHERE usn=%s", (usn,))
    con.commit()
    con.close()

    return jsonify({"status": "success"})


# ----------------------------------------------------------
# PHOTO UPLOAD — UID BASED
# ----------------------------------------------------------
@app.route("/api/upload_photo", methods=["POST"])
def upload_photo():
    uid = (request.form.get("uid") or "").strip().upper()
    file = request.files.get("photo")

    if not uid or not file:
        return jsonify({"status": "error", "message": "UID and photo required"}), 400

    if not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Invalid file type"}), 400

    filename = secure_filename(uid + ".jpg")
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE students SET photo=%s WHERE uid=%s", (filename, uid))
    con.commit()
    con.close()

    return jsonify({"status": "success", "filename": filename})


# ----------------------------------------------------------
# HTML ROUTES
# ----------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/student_login")
def student_login_page():
    return render_template("student_login.html")

@app.route("/student")
def student_page():
    return render_template("student.html")

@app.route("/admin_login")
def admin_login_page_html():
    return render_template("admin_login.html")

@app.route("/admin")
def admin_page():
    return render_template("admin.html")

@app.route("/kiosk")
def kiosk_page():
    return render_template("kiosk.html")


# ----------------------------------------------------------
# RUN
# ----------------------------------------------------------
if __name__ == "__main__":
    print("Flask app started successfully")
    app.run(host="0.0.0.0", port=5001, debug=False)
