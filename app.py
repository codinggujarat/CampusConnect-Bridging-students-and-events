# app.py
import os
import uuid
import csv
import qrcode
import razorpay
import random
import pandas as pd
from dotenv import load_dotenv
from flask import (
    Flask, render_template, jsonify, request, redirect,
    url_for, flash, session, send_file
)
from collections import Counter
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet
from twilio.rest import Client

# ---------------- Load env ----------------
load_dotenv()

# ---------------- Flask app ----------------
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', 'sqlite:///students.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ---------------- Mail config (kept) ----------------
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')

# ---------------- DB ----------------
db = SQLAlchemy(app)

# ---------------- Razorpay client (test or live via env) ----------------
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')
if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
    raise RuntimeError("Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env")
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# ---------------- Twilio (WhatsApp) ----------------
TWILIO_SID = os.getenv('TWILIO_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP = os.getenv('TWILIO_WHATSAPP', 'whatsapp:+14155238886')
if TWILIO_SID and TWILIO_AUTH_TOKEN:
    twilio_client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
else:
    twilio_client = None

# ---------------- Models ----------------
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unique_id = db.Column(db.String(100), unique=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(100))
    semester = db.Column(db.Integer)
    mobile_number = db.Column(db.String(15))
    attended = db.Column(db.Boolean, default=False)
    upi_id = db.Column(db.String(100))
    transaction_id = db.Column(db.String(100))          # Razorpay payment_id
    razorpay_order_id = db.Column(db.String(100))        # Razorpay order_id
    payment_status = db.Column(db.String(50))            # "Paid" | "Refunded" | "Failed"
    refund_id = db.Column(db.String(100))                # Razorpay refund id
    refunded = db.Column(db.Boolean, default=False)

# ---------------- CSV paths & helpers ----------------
CSV_PATH = os.path.join('static', 'csv_exports', 'registrations.csv')
os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)

def append_to_csv(name, email, semester, unique_id, mobile_number, upi_id=None, transaction_id=None, status="Paid"):
    """
    Append a registration entry to CSV.
    Ensures correct headers on top if the file doesn't exist.
    """
    # Check if CSV exists
    file_exists = os.path.isfile(CSV_PATH)

    # Ensure directory exists
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)

    # Open CSV in append mode
    with open(CSV_PATH, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        # Write header if file doesn't exist
        if not file_exists:
            writer.writerow([
                'Name',
                'Email',
                'Semester',
                'Mobile Number',
                'Event ID',
                'Payment Status',
                'Paid via',
                'Amount',
                'UPI ID',
                'Transaction ID',
                'Attendance'
            ])

        # Write student row
        writer.writerow([
            name,
            email,
            semester,
            mobile_number,
            unique_id,
            status,
            'Razorpay',
            '₹100',
            upi_id or 'N/A',
            transaction_id or 'N/A',
            'Not Marked'
        ])


def update_attendance_in_csv(uid):
    if not os.path.isfile(CSV_PATH):
        return
    rows = []
    with open(CSV_PATH, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        for row in reader:
            if row[4] == uid:
                row[-1] = 'Present'
            rows.append(row)
    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

def update_payment_status_in_csv(uid, new_status):
    if not os.path.isfile(CSV_PATH):
        return
    rows = []
    with open(CSV_PATH, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        for row in reader:
            if row[4] == uid:
                row[5] = new_status
            rows.append(row)
    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

# ---------------- Utilities ----------------
def admin_required():
    """Helper to check admin session. Returns True if logged in."""
    return session.get("admin_logged_in") is True

# ---------------- WhatsApp confirmation ----------------
def send_whatsapp_confirmation(mobile_number, name, event_id):
    if not twilio_client:
        print("Twilio not configured; skipping WhatsApp message.")
        return
    try:
        student = Student.query.filter_by(unique_id=event_id).first()
        if not student:
            print(f"No student found for Event ID: {event_id}")
            return

        payment_info = ""
        if student.payment_status == "Paid":
            payment_info = (f"\n\n💳 Payment Details:\n"
                            f"Amount Paid: ₹100\n"
                            f"UPI ID: {student.upi_id or 'N/A'}\n"
                            f"Transaction ID: {student.transaction_id or 'N/A'}\n"
                            f"Payment Status: {student.payment_status or 'Pending'}")
        else:
            payment_info = "\n\n💰 Semester 1 students attend for FREE or refunds will be processed later."

        message_body = (f"🎉 Hi {name}! Your spot is confirmed ✅\n"
                        f"Event ID: {event_id}."
                        f"{payment_info}\n\nSee you there! 🎯")

        msg = twilio_client.messages.create(
            from_=TWILIO_WHATSAPP,
            body=message_body,
            to=f'whatsapp:+91{mobile_number}'
        )
        print("WhatsApp SID:", msg.sid)
    except Exception as e:
        print("Error sending WhatsApp:", e)

# ---------------- QR / PDF helpers ----------------
def generate_qr_code(uid):
    folder = os.path.join('static', 'qr_codes')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{uid}.png')
    img = qrcode.make(f'{uid}')
    img.save(path)

def generate_pdf(name, email, semester, uid, paid=False, upi_id=None, transaction_id=None):
    folder = os.path.join('static', 'pdfs')
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, f'{uid}.pdf')
    qr_path = os.path.join('static', 'qr_codes', f'{uid}.png')

    doc = SimpleDocTemplate(filepath)
    styles = getSampleStyleSheet()
    flowables = []

    flowables.append(Paragraph("🎉 Event Registration Confirmation", styles['Title']))
    flowables.append(Spacer(1, 12))
    flowables.append(Paragraph(f"Name: {name}", styles['Normal']))
    flowables.append(Paragraph(f"Email: {email}", styles['Normal']))
    flowables.append(Paragraph(f"Semester: {semester}", styles['Normal']))
    flowables.append(Paragraph(f"Event ID: {uid}", styles['Normal']))
    flowables.append(Spacer(1, 12))

    if paid:
        flowables.append(Paragraph("✅ Payment Status: Paid", styles['Normal']))
        flowables.append(Paragraph("💳 Paid via: Razorpay", styles['Normal']))
        flowables.append(Paragraph(f"📌 Transaction ID: {transaction_id or 'N/A'}", styles['Normal']))
        flowables.append(Paragraph(f"🆔 UPI ID: {upi_id or 'N/A'}", styles['Normal']))
        flowables.append(Paragraph("📌 Amount: ₹100", styles['Normal']))
    else:
        flowables.append(Paragraph("ℹ️ Registration recorded.", styles['Normal']))

    if semester == 1:
        flowables.append(Paragraph("ℹ️ Sem 1 students are eligible for a ₹100 refund after the event (admin processed).", styles['Normal']))
    flowables.append(Spacer(1, 12))

    flowables.append(Paragraph("Scan this QR to verify entry:", styles['Normal']))
    if os.path.exists(qr_path):
        flowables.append(Image(qr_path, width=150, height=150))

    doc.build(flowables)

# ---------------- Routes ----------------
@app.route('/')
def home():
    return render_template('register.html')

# Registration + payment start
@app.route('/pay', methods=['POST'])
def pay():
    name = request.form.get('name')
    email = request.form.get('email')
    semester = int(request.form.get('semester', 1))
    mobile_number = request.form.get('mobile_number')
    unique_id = f"MSCCAIT2025-{str(uuid.uuid4())[:8]}"

    # duplicate check
    existing = Student.query.filter((Student.email == email) | (Student.mobile_number == mobile_number)).first()
    if existing:
        flash("This email or mobile number is already registered.", "danger")
        return redirect(url_for('home'))

    # Save registration temporarily in session
    session['registration'] = {
        'name': name,
        'email': email,
        'semester': semester,
        'mobile_number': mobile_number,
        'unique_id': unique_id
    }

    # Create Razorpay order (₹100 -> 10000 paise)
    order = razorpay_client.order.create(dict(amount=10000, currency='INR', payment_capture='1'))
    session['registration']['razorpay_order_id'] = order['id']

    return render_template('payment.html',
                           order_id=order['id'],
                           amount=100,
                           razorpay_key=RAZORPAY_KEY_ID,
                           name=name,
                           email=email)

# Payment success (called by frontend after razorpay)
@app.route('/payment-success', methods=['POST'])
def payment_success():
    data = session.get('registration')
    if not data:
        flash("Session expired. Please register again.", "danger")
        return redirect(url_for('home'))

    # prevent duplicates
    existing = Student.query.filter((Student.email == data['email']) | (Student.mobile_number == data['mobile_number'])).first()
    if existing:
        flash("This email or mobile number is already registered.", "danger")
        session.pop('registration', None)
        return redirect(url_for('home'))

    razorpay_payment_id = request.form.get('razorpay_payment_id')
    upi_id = request.form.get('upi_id')  # optional

    student = Student(
        name=data['name'],
        email=data['email'],
        semester=data['semester'],
        mobile_number=data['mobile_number'],
        unique_id=data['unique_id'],
        upi_id=upi_id if upi_id else "N/A",
        transaction_id=razorpay_payment_id,
        razorpay_order_id=data.get('razorpay_order_id'),
        payment_status="Paid",
        refunded=False
    )
    db.session.add(student)
    db.session.commit()
    session.pop('registration', None)

    # generate QR, PDF, CSV, WhatsApp
    generate_qr_code(student.unique_id)
    generate_pdf(student.name, student.email, student.semester, student.unique_id, paid=True,
                 upi_id=student.upi_id, transaction_id=student.transaction_id)
    append_to_csv(student.name, student.email, student.semester, student.unique_id,
                  student.mobile_number, upi_id=student.upi_id, transaction_id=student.transaction_id, status="Paid")

    # WhatsApp
    send_whatsapp_confirmation(student.mobile_number, student.name, student.unique_id)

    flash("Payment successful! Download QR & PDF from the next page.", "success")
    return redirect(url_for('download_all', uid=student.unique_id))

# downloads
@app.route('/download-all/<uid>')
def download_all(uid):
    return render_template('download.html', uid=uid)

@app.route('/get-qr/<uid>')
def get_qr(uid):
    path = os.path.join('static', 'qr_codes', f'{uid}.png')
    return send_file(path, as_attachment=True)

@app.route('/get-pdf/<uid>')
def get_pdf(uid):
    path = os.path.join('static', 'pdfs', f'{uid}.pdf')
    return send_file(path, as_attachment=True)

# verify QR attendance
@app.route('/verify/<uid>')
def verify(uid):
    student = Student.query.filter_by(unique_id=uid).first()
    if not student:
        return render_template('verify.html', status="error", message="Invalid QR or student not registered.")

    if student.attended:
        return render_template('verify.html', status="warning", message=f"{student.name} has already attended.")

    student.attended = True
    db.session.commit()
    update_attendance_in_csv(uid)
    return render_template('verify.html', status="success", message=f"Attendance marked for {student.name} (Sem {student.semester}).")

@app.route('/verify')
def verify_redirect():
    uid = request.args.get('uid')
    return redirect(url_for('verify', uid=uid))

# scanner pages
@app.route('/scanner')
def scanner():
    return render_template('scanner.html')

@app.route('/scan')
def scan_qr_page():
    return render_template('scan.html')

# ---------------- Admin / Management ----------------
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', '@291104AMANNAYAK')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '@291104AMANNAYAK')

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            flash("Admin logged in", "success")
            return redirect(url_for('admin_panel'))
        else:
            flash("Invalid credentials", "danger")
    return render_template('admin_login.html')

@app.route('/admin')
def admin_panel():
    if not admin_required():
        return redirect(url_for('admin_login'))
    students = Student.query.order_by(Student.id.desc()).all()
    return render_template('admin_panel.html', students=students)

@app.route('/admin-dashboard')
def admin_dashboard():
    if not admin_required():
        return redirect(url_for('admin_login'))
    students = Student.query.all()
    total_present = sum(1 for s in students if s.attended)
    total_absent = sum(1 for s in students if not s.attended)
    total_students = len(students)
    sem_stats = {}
    for sem in range(1, 7):
        sem_present = sum(1 for s in students if s.semester == sem and s.attended)
        sem_absent = sum(1 for s in students if s.semester == sem and not s.attended)
        sem_stats[sem] = {"present": sem_present, "absent": sem_absent}
    return render_template('admin_dashboard.html', total_present=total_present, total_absent=total_absent, total_students=total_students, sem_stats=sem_stats)

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out", "info")
    return redirect(url_for('home'))

# Unified delete route (used by admin_panel & other pages)
@app.route('/delete/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    if not admin_required():
        return redirect(url_for('admin_login'))
    student = Student.query.get_or_404(student_id)
    try:
        db.session.delete(student)
        db.session.commit()
        flash("Student deleted successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Error deleting student: " + str(e), "danger")
    return redirect(request.referrer or url_for('admin_panel'))

# Separate delete used from refund page if templates call this name
@app.route('/admin/delete_refund/<int:student_id>', methods=['POST'])
def delete_student_refund(student_id):
    if not admin_required():
        return redirect(url_for('admin_login'))
    student = Student.query.get_or_404(student_id)
    try:
        db.session.delete(student)
        db.session.commit()
        flash("Student deleted from refunds list.", "success")
    except Exception as e:
        db.session.rollback()
        flash("Error deleting student: " + str(e), "danger")
    return redirect(request.referrer or url_for('sem1_refunds'))

# ---------------- Refund pages & actions ----------------
@app.route('/admin/refunds')
def sem1_refunds():
    if not admin_required():
        return redirect(url_for('admin_login'))
    sem1_students = Student.query.filter_by(semester=1).all()
    return render_template('sem1_refunds.html', students=sem1_students)

@app.route('/process_refund/<int:student_id>', methods=['POST'])
def process_refund(student_id):
    # Fetch student by ID
    student = Student.query.get_or_404(student_id)

    # Check if student has already been refunded
    if student.refunded:
        flash(f"Refund already processed for {student.name}!", "info")
        return redirect(url_for('student_info'))

    # Check if student has paid
    if student.payment_status != "Paid":
        flash("Refund cannot be processed. Payment not completed!", "danger")
        return redirect(url_for('student_info'))

    try:
        # Razorpay Refund API Call
        refund = razorpay_client.payment.refund(student.transaction_id, {
            "amount": 10000  # Amount in paise (₹100)
        })

        # Update student record after refund success
        student.refunded = True
        student.payment_status = "Refunded"
        db.session.commit()

        flash(f"Refund processed for {student.name} (Sem 1) ✅", "success")
    except Exception as e:
        flash(f"Refund failed: {str(e)}", "danger")

    return redirect(url_for('sem1_refunds'))

# ---------------- Charts & dashboard helpers ----------------
def _clean_csv(file_path):
    """
    Try to read and rewrite a cleaned CSV (skips malformed rows).
    Returns True if file exists and was (re)written or already ok.
    """
    try:
        if not os.path.exists(file_path):
            return False

        # Try the modern, safe read (skip bad lines)
        try:
            df = pd.read_csv(file_path, header=0, on_bad_lines='skip')
        except TypeError:
            # older pandas: try engine='python' fallback
            df = pd.read_csv(file_path, header=0, engine='python', error_bad_lines=False)

        # If no columns or no semester-like column, just return True (we will fallback later)
        if df.shape[0] == 0:
            # nothing to clean
            return True

        # normalize column names
        df.columns = [str(c).strip() for c in df.columns]

        # write back cleaned CSV (overwrites)
        df.to_csv(file_path, index=False)
        return True
    except Exception as e:
        # log and continue — don't crash
        app.logger.exception("CSV cleaning failed")
        return False


@app.route('/chart_data')
def chart_data():
    file_path = os.path.join(app.root_path, 'static', 'csv_exports', 'registrations.csv')

    # If CSV missing — return empty chart payload
    if not os.path.exists(file_path):
        app.logger.info("chart_data: CSV not found")
        return jsonify({"labels": [], "values": [], "legend": "Students per Semester"})

    # Attempt to clean CSV to avoid parser errors
    _clean_csv(file_path)

    # Try reading with pandas first (robustly)
    try:
        try:
            df = pd.read_csv(file_path, header=0, on_bad_lines='skip')
        except TypeError:
            # older pandas fallback
            df = pd.read_csv(file_path, header=0, engine='python', error_bad_lines=False)

        # Normalize column names for detection
        cols = [str(c).strip().lower() for c in df.columns]

        # Determine semester column index:
        sem_idx = None
        if 'semester' in cols:
            sem_idx = cols.index('semester')
            sem_series = pd.to_numeric(df.iloc[:, sem_idx], errors='coerce').dropna().astype(int)
        else:
            # fallback to 3rd column (index 2) if exists (your CSV used 3rd column originally)
            if df.shape[1] >= 3:
                sem_series = pd.to_numeric(df.iloc[:, 2], errors='coerce').dropna().astype(int)
            else:
                app.logger.warning("chart_data: no semester column and not enough columns")
                return jsonify({"labels": [], "values": [], "legend": "Students per Semester"})

        if sem_series.empty:
            app.logger.info("chart_data: semester column empty after cleaning")
            return jsonify({"labels": [], "values": [], "legend": "Students per Semester"})

        semester_counts = sem_series.value_counts().sort_index()
        labels = [f"Sem {int(s)}" for s in semester_counts.index.tolist()]
        values = semester_counts.values.tolist()

        return jsonify({"labels": labels, "values": values, "legend": "Students per Semester"})

    except Exception as e_pandas:
        # Pandas failed — do tolerant manual parse
        app.logger.exception("chart_data: pandas read failed, falling back to manual parse")
        try:
            semesters = []
            with open(file_path, 'r', encoding='utf-8', newline='') as f:
                import csv
                reader = csv.reader(f)
                headers = next(reader, None)
                sem_idx = None
                if headers:
                    hdrs = [h.strip().lower() for h in headers]
                    if 'semester' in hdrs:
                        sem_idx = hdrs.index('semester')
                if sem_idx is None:
                    sem_idx = 2  # fallback to 3rd column

                for row in reader:
                    if len(row) > sem_idx:
                        val = row[sem_idx].strip()
                        if val != '':
                            try:
                                si = int(float(val))
                                semesters.append(si)
                            except:
                                continue

            if not semesters:
                return jsonify({"labels": [], "values": [], "legend": "Students per Semester"})

            counter = Counter(semesters)
            keys = sorted(counter.keys())
            labels = [f"Sem {k}" for k in keys]
            values = [counter[k] for k in keys]
            return jsonify({"labels": labels, "values": values, "legend": "Students per Semester"})
        except Exception as e_manual:
            app.logger.exception("chart_data: manual parse also failed")
            return jsonify({"labels": [], "values": [], "legend": "Students per Semester"})
        
@app.route('/dashboard_data')
def dashboard_data():
    paid_not_refunded = db.session.query(func.count(Student.id))\
        .filter(Student.payment_status == "Paid", Student.refunded == False)\
        .scalar() or 0

    refunded_count = db.session.query(func.count(Student.id))\
        .filter(Student.refunded == True)\
        .scalar() or 0

    total_students = db.session.query(func.count(Student.id)).scalar() or 0
    total_amount_collected = paid_not_refunded * 100
    total_amount_refunded = refunded_count * 100

    sem_counts = {}
    for sem in range(1, 7):
        count = db.session.query(func.count(Student.id))\
            .filter(Student.semester == sem).scalar() or 0
        sem_counts[f"Sem{sem}"] = count

    # Flat arrays for accurate numbers
    total_students_values = [total_students] * 7
    paid_not_refunded_values = [paid_not_refunded] * 7
    total_amount_collected_values = [total_amount_collected] * 7
    refunded_count_values = [refunded_count] * 7
    total_amount_refunded_values = [total_amount_refunded] * 7

    return jsonify({
        "total_students": total_students,
        "paid_not_refunded": paid_not_refunded,
        "total_amount_collected": total_amount_collected,
        "refunded_count": refunded_count,
        "total_amount_refunded": total_amount_refunded,
        "sem_counts": sem_counts,
        "total_students_values": total_students_values,
        "paid_not_refunded_values": paid_not_refunded_values,
        "total_amount_collected_values": total_amount_collected_values,
        "refunded_count_values": refunded_count_values,
        "total_amount_refunded_values": total_amount_refunded_values
    })

# ---------------- Init & Run ----------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
