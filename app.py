from flask import Flask, request, jsonify, render_template
import sqlite3
from werkzeug.utils import secure_filename
import os
from flask_cors import CORS
import shutil

app = Flask(__name__)
CORS(app)

# ------------------------------------------
# FILE UPLOAD SETTINGS
# ------------------------------------------
UPLOAD_FOLDER = "static/photos"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ------------------------------------------
# DATABASE (use /tmp on Render so it's writable)
# ------------------------------------------
LOCAL_DB = "database.db"     # repo copy (optional)
DB_PATH = "/tmp/database.db" # runtime DB file used by the app

# If runtime DB doesn't exist, copy the local DB from repo into /tmp
if not os.path.exists(DB_PATH):
    if os.path.exists(LOCAL_DB):
        try:
            shutil.copy(LOCAL_DB, DB_PATH)
            print("Copied local database.db to /tmp/database.db")
        except Exception as e:
            print("Failed to copy local DB:", e)
    else:
        # No local DB present in repo — create new empty DB file by connecting once
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.close()
            print("Created new DB at /tmp/database.db")
        except Exception as e:
            print("Failed to create DB in /tmp:", e)


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ------------------------------------------
# HEALTH CHECK
# ------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


# ------------------------------------------
# STUDENT CRUD + INFO
# ------------------------------------------
@app.route("/api/students")
def get_students():
    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM students")
    rows = cur.fetchall()
    con.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/add_student", methods=["POST"])
def add_student():
    data = request.json or {}
    uid = (data.get("uid") or "").strip().upper()
    name = data.get("name")
    balance = data.get("balance", 0)
    pin = data.get("pin", "1234")

    if not uid or not name:
        return jsonify({"status": "error", "message": "UID & Name required"}), 400

    con = get_db()
    cur = con.cursor()
    try:
        cur.execute("INSERT INTO students (uid, name, balance, pin) VALUES (?, ?, ?, ?)",
                    (uid, name, balance, pin))
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({"status": "error", "message": "UID already exists"}), 400
    except Exception as e:
        con.close()
        return jsonify({"status": "error", "message": str(e)}), 500

    con.close()
    return jsonify({"status": "success", "message": "Student added"})


@app.route("/api/student/<uid>")
def student_info(uid):
    uid = (uid or "").strip().upper()
    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM students WHERE UPPER(uid) = ?", (uid,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404

    student_data = dict(s)

    cur.execute("SELECT * FROM transactions WHERE uid=? ORDER BY id DESC LIMIT 5", (uid,))
    tx = [dict(t) for t in cur.fetchall()]

    con.close()
    return jsonify({"status": "success", "student": student_data, "transactions": tx})


# ------------------------------------------
# PAYMENT + TOPUP
# ------------------------------------------
@app.route("/api/pay", methods=["POST"])
def pay():
    data = request.json or {}
    uid = (data.get("uid") or "").strip().upper()
    amount = data.get("amount")

    if not uid or amount is None:
        return jsonify({"status": "error", "message": "UID + Amount required"}), 400

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM students WHERE UPPER(uid)=?", (uid,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404
    if s["blocked"] == 1:
        con.close()
        return jsonify({"status": "error", "message": "Card blocked"}), 403
    if s["balance"] < amount:
        cur.execute("INSERT INTO transactions (uid, amount, status) VALUES (?, ?, ?)",
                    (uid, amount, "failed_insufficient"))
        con.commit()
        con.close()
        return jsonify({"status": "error", "message": "Insufficient balance"}), 400

    new_balance = s["balance"] - amount

    cur.execute("UPDATE students SET balance=? WHERE uid=?", (new_balance, uid))
    cur.execute("INSERT INTO transactions (uid, amount, status) VALUES (?, ?, ?)",
                (uid, amount, "success"))
    con.commit()
    con.close()

    return jsonify({"status": "success", "new_balance": new_balance})


@app.route("/api/add_balance", methods=["POST"])
def add_balance():
    data = request.json or {}
    uid = (data.get("uid") or "").strip().upper()
    amount = data.get("amount")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM students WHERE UPPER(uid)=?", (uid,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404

    new_balance = s["balance"] + amount

    cur.execute("UPDATE students SET balance=? WHERE uid=?", (new_balance, uid))
    cur.execute("INSERT INTO transactions (uid, amount, status) VALUES (?, ?, ?)",
                (uid, amount, "topup"))
    con.commit()
    con.close()

    return jsonify({"status": "success", "new_balance": new_balance})


# ------------------------------------------
# BLOCK / UNBLOCK
# ------------------------------------------
@app.route("/api/block_card", methods=["POST"])
def block_card():
    uid = (request.json.get("uid") or "").strip().upper()

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE students SET blocked=1 WHERE UPPER(uid)=?", (uid,))
    con.commit()
    con.close()

    return jsonify({"status": "success"})


@app.route("/api/unblock_card", methods=["POST"])
def unblock_card():
    uid = (request.json.get("uid") or "").strip().upper()

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE students SET blocked=0 WHERE UPPER(uid)=?", (uid,))
    con.commit()
    con.close()

    return jsonify({"status": "success"})


# ------------------------------------------
# STUDENT LOGIN
# ------------------------------------------
@app.route("/api/login", methods=["POST"])
def student_login_api():
    data = request.json or {}
    uid = (data.get("uid") or "").strip().upper()
    pin = (data.get("pin") or "").strip()

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM students WHERE UPPER(uid)=?", (uid,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404
    if (s["pin"] or "").strip() != pin:
        con.close()
        return jsonify({"status": "error", "message": "Incorrect PIN"}), 403

    con.close()
    return jsonify({"status": "success"})


@app.route("/api/change_pin", methods=["POST"])
def change_pin():
    data = request.json or {}
    uid = (data.get("uid") or "").strip().upper()
    old_pin = (data.get("old_pin") or "").strip()
    new_pin = (data.get("new_pin") or "").strip()

    con = get_db()
    cur = con.cursor()
    cur.execute("SELECT * FROM students WHERE UPPER(uid)=?", (uid,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"status": "error", "message": "Student not found"}), 404
    if (s["pin"] or "").strip() != old_pin:
        con.close()
        return jsonify({"status": "error", "message": "Old PIN incorrect"}), 403

    cur.execute("UPDATE students SET pin=? WHERE UPPER(uid)=?", (new_pin, uid))
    con.commit()
    con.close()

    return jsonify({"status": "success", "message": "PIN updated"})


# ------------------------------------------
# ADMIN LOGIN
# ------------------------------------------
@app.route("/api/admin_login", methods=["POST"])
def admin_login():
    data = request.json or {}
    username = data.get("username")
    password = data.get("password")

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT * FROM admins WHERE username=? AND password=? AND active=1",
                (username, password))
    admin = cur.fetchone()

    if admin is None:
        con.close()
        return jsonify({"status": "error", "message": "Invalid credentials"}), 401

    cur.execute("UPDATE admins SET last_login=CURRENT_TIMESTAMP WHERE username=?", (username,))
    con.commit()
    con.close()

    return jsonify({"status": "success"})


# ------------------------------------------
# UPLOAD PHOTO (ONLY ONE FUNCTION NOW)
# ------------------------------------------
@app.route("/api/upload_photo", methods=["POST"])
def upload_photo():
    uid = (request.form.get("uid") or "").strip().upper()
    file = request.files.get("photo")

    if not uid or not file:
        return jsonify({"status": "error", "message": "UID and photo required"}), 400

    if not allowed_file(file.filename):
        return jsonify({"status": "error", "message": "Invalid file type"}), 400

    filename = secure_filename(uid + ".jpg")  # force same name
    save_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(save_path)

    con = get_db()
    cur = con.cursor()
    cur.execute("UPDATE students SET photo=? WHERE UPPER(uid)=?", (filename, uid))
    con.commit()
    con.close()

    return jsonify({"status": "success", "filename": filename})


# ------------------------------------------
# HTML ROUTES
# ------------------------------------------
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


@app.route("/features")
def features_page():
    return render_template("features.html")


@app.route("/about")
def about_page():
    return render_template("about.html")


@app.route("/contact")
def contact_page():
    return render_template("contact.html")


@app.route("/kiosk")
def kiosk_page():
    return render_template("kiosk.html")


# ------------------------------------------
# RFID VERIFY — FOR ESP32 (kept for compatibility)
# ------------------------------------------
@app.route("/verify", methods=["GET"])
def verify_card():
    uid = (request.args.get("uid") or "").strip().upper()

    if not uid:
        return jsonify({"ok": False, "error": "UID missing"}), 400

    con = get_db()
    cur = con.cursor()

    cur.execute("SELECT name, balance, blocked FROM students WHERE UPPER(uid)=?", (uid,))
    s = cur.fetchone()
    con.close()

    if s is None:
        return jsonify({"ok": False, "error": "User not found"}), 404

    if s["blocked"] == 1:
        return jsonify({"ok": False, "error": "Card blocked"}), 403

    return jsonify({
        "ok": True,
        "name": s["name"],
        "balance": s["balance"]
    })


# ------------------------------------------
# RFID DEDUCT — FOR ESP32 (kept for compatibility)
# ------------------------------------------
@app.route("/deduct", methods=["POST"])
def deduct_amount():
    data = request.json or {}
    uid = (data.get("uid") or "").strip().upper()
    amount = data.get("amount")

    if not uid or amount is None:
        return jsonify({"ok": False, "error": "UID and Amount required"}), 400

    con = get_db()
    cur = con.cursor()

    # Get student
    cur.execute("SELECT name, balance, blocked FROM students WHERE UPPER(uid)=?", (uid,))
    s = cur.fetchone()

    if s is None:
        con.close()
        return jsonify({"ok": False, "error": "User not found"}), 404

    if s["blocked"] == 1:
        con.close()
        return jsonify({"ok": False, "error": "Card blocked"}), 403

    # Check balance
    if s["balance"] < amount:
        cur.execute("INSERT INTO transactions (uid, amount, status) VALUES (?, ?, ?)",
                    (uid, amount, "failed_insufficient"))
        con.commit()
        con.close()
        return jsonify({"ok": False, "error": "Insufficient balance"}), 400

    # Deduct
    new_balance = s["balance"] - amount

    cur.execute("UPDATE students SET balance=? WHERE UPPER(uid)=?", (new_balance, uid))
    cur.execute("INSERT INTO transactions (uid, amount, status) VALUES (?, ?, ?)",
                (uid, amount, "success"))
    con.commit()
    con.close()

    return jsonify({
        "ok": True,
        "message": f"Deducted {amount}",
        "balance": new_balance
    })


# ------------------------------------------
# RUN SERVER
# ------------------------------------------
if __name__ == "__main__":
    print("Flask app started successfully")
    app.run(host="0.0.0.0", port=5001, debug=False)
