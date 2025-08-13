from flask import Flask, render_template,jsonify, request, redirect, url_for, flash, session, send_file
from flask import Flask
from flask_mail import Mail, Message
import pandas as pd
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from sqlalchemy import func
import razorpay
import random
import uuid
import qrcode
import csv
import os
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet
from twilio.rest import Client  # New import for WhatsApp

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///students.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Flask-Mail settings (AFTER app = Flask(__name__))
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER')
mail = Mail(app)  # initialize mail AFTER config

db = SQLAlchemy(app)

# Razorpay
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# Twilio WhatsApp settings

TWILIO_SID = os.getenv('TWILIO_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_WHATSAPP = os.getenv('TWILIO_WHATSAPP', 'whatsapp:+14155238886')  # Twilio sandbox
twilio_client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)

# ------------------ Function to send confirmation ------------------

def send_whatsapp_confirmation(mobile_number, name, event_id):
    try:
        # Fetch student from DB using event_id (assuming event_id is unique_id)
        student = Student.query.filter_by(unique_id=event_id).first()
        
        if not student:
            print(f"No student found for Event ID: {event_id}")
            return
        
        # Build payment info message
        payment_info = ""
        if student.semester >= 2:
            payment_info = (
                f"\n\n💳 Payment Details:\n"
                f"Amount Paid: ₹100\n"
                f"UPI ID: {student.upi_id or 'N/A'}\n"
                f"Transaction ID: {student.transaction_id or 'N/A'}\n"
                f"Payment Status: {student.payment_status or 'Pending'}"
            )
        else:
            payment_info = "\n\n💰 Semester 1 students attend for FREE 🎉"

        client = Client(TWILIO_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            from_=TWILIO_WHATSAPP,
            body=(
                f"🎉 Hi {name}! Welcome aboard 🚀\n"
                f"Your spot with *Campus Connect* is locked in ✅\n"
                f"Event ID: {event_id}."
                f"{payment_info}\n\n"
                f"Get ready for an amazing experience! 🎯"
            ),
            to=f'whatsapp:+91{mobile_number}'  # Change +91 if not India
        )
        print(f"WhatsApp message sent: {message.sid}")

    except Exception as e:
        print(f"Error sending WhatsApp message: {e}")



# ----------------------------- Models -----------------------------
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unique_id = db.Column(db.String(100), unique=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(100))
    semester = db.Column(db.Integer)
    mobile_number = db.Column(db.String(15))  # New field
    attended = db.Column(db.Boolean, default=False)
    upi_id = db.Column(db.String(100))
    transaction_id = db.Column(db.String(100))
    payment_status = db.Column(db.String(50))

# ----------------------------- CSV Helpers -----------------------------
CSV_PATH = os.path.join('static', 'csv_exports', 'registrations.csv')
os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)

def append_to_csv(name, email, semester, unique_id, mobile_number, paid=False, upi_id=None):
    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(['Name', 'Email', 'Semester', 'Mobile Number', 'Event ID', 'Payment Status', 'Paid via', 'Amount', 'UPI ID', 'Attendance'])

        if paid:
            writer.writerow([name, email, semester, mobile_number, unique_id, 'Paid', 'Razorpay', '₹100', upi_id, 'Not Marked'])
        else:
            writer.writerow([name, email, semester, mobile_number, unique_id, '-', '-', '-', '-', 'Not Marked'])

def update_attendance_in_csv(uid):
    if not os.path.isfile(CSV_PATH):
        return
    rows = []
    with open(CSV_PATH, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        for row in reader:
            if row[4] == uid:  # Event ID column changed due to new mobile column
                row[-1] = 'Present'
            rows.append(row)
    with open(CSV_PATH, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

# ----------------------------- Routes -----------------------------
@app.route('/')
def home():
    return render_template('register.html')

@app.route('/pay', methods=['POST'])
def pay():
    name = request.form['name']
    email = request.form['email']
    semester = int(request.form['semester'])
    mobile_number = request.form['mobile_number']
    unique_id = f"MSCCAIT2025-{str(uuid.uuid4())[:8]}"

    if semester == 1:
        student = Student(name=name, email=email, semester=semester, mobile_number=mobile_number, unique_id=unique_id)
        db.session.add(student)
        db.session.commit()

        generate_qr_code(unique_id)
        generate_pdf(name, email, semester, unique_id)
        append_to_csv(name, email, semester, unique_id, mobile_number, paid=False)

        send_whatsapp_confirmation(mobile_number, name, unique_id)

        flash("Free registration successful!", "success")
        return redirect(url_for('download_all', uid=unique_id))
    else:
        session['registration'] = {
            'name': name,
            'email': email,
            'semester': semester,
            'mobile_number': mobile_number,
            'unique_id': unique_id
        }
        order = razorpay_client.order.create(dict(amount=10000, currency='INR', payment_capture='1'))
        return render_template('payment.html',
                               order_id=order['id'],
                               amount=100,
                               razorpay_key=RAZORPAY_KEY_ID,
                               name=name,
                               email=email)

@app.route('/payment-success', methods=['POST'])
def payment_success():
    data = session.get('registration')
    if not data:
        flash("Session expired. Please register again.", "error")
        return redirect(url_for('home'))

    razorpay_payment_id = request.form.get('razorpay_payment_id')
    upi_id = request.form.get('upi_id')
    payment_status = "Paid"

    student = Student(
        **data,
        transaction_id=razorpay_payment_id,
        upi_id=upi_id if upi_id else "N/A",
        payment_status=payment_status
    )
    db.session.add(student)
    db.session.commit()
    session.pop('registration')

    generate_qr_code(student.unique_id)
    generate_pdf(student.name, student.email, student.semester, student.unique_id, paid=True,
                 upi_id=student.upi_id, transaction_id=student.transaction_id)
    append_to_csv(student.name, student.email, student.semester, student.unique_id, student.mobile_number, paid=True,
                  upi_id=student.upi_id)

    send_whatsapp_confirmation(student.mobile_number, student.name, student.unique_id)

    flash("Payment successful! Download your QR and PDF below.", "success")
    return redirect(url_for('download_all', uid=student.unique_id))

# ----------------------------- QR / PDF -----------------------------
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
        flowables.append(Paragraph(f"📌 Transaction ID: {transaction_id}", styles['Normal']))
        flowables.append(Paragraph(f"🆔 UPI ID: {upi_id}", styles['Normal']))
        flowables.append(Paragraph("📌 Amount: ₹100", styles['Normal']))
        flowables.append(Spacer(1, 12))

    flowables.append(Paragraph("Scan this QR to verify entry:", styles['Normal']))
    flowables.append(Image(qr_path, width=150, height=150))

    doc.build(flowables)

# ----------------------------- Downloads -----------------------------
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

# ----------------------------- Attendance QR Verify -----------------------------
@app.route('/verify/<uid>')
def verify(uid):
    student = Student.query.filter_by(unique_id=uid).first()

    if not student:
        return render_template(
            'verify.html',
            status="error",
            message=" Invalid QR or student not registered."
        )

    if student.attended:
        return render_template(
            'verify.html',
            status="warning",
            message=f" {student.name} has already attended."
        )

    # Mark attendance
    student.attended = True
    db.session.commit()
    update_attendance_in_csv(uid)

    return render_template(
        'verify.html',
        status="success",
        message=f" Attendance marked for {student.name} (Sem {student.semester})."
    )

@app.route('/verify')
def verify_redirect():
    uid = request.args.get('uid')
    return redirect(url_for('verify', uid=uid))

# ----------------------------- Scanner Page -----------------------------
@app.route('/scanner')
def scanner():
    return render_template('scanner.html')

@app.route('/scan')
def scan_qr_page():
    return render_template('scan.html')

# ----------------------------- Admin Panel -----------------------------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "1234"

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        else:
            flash("Invalid credentials", "danger")
    return render_template("admin_login.html")

@app.route('/admin')
def admin_panel():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    students = Student.query.all()
    return render_template("admin_panel.html", students=students)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route('/delete/<int:id>', methods=['POST'])
def delete_student(id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    student = Student.query.get_or_404(id)
    db.session.delete(student)
    db.session.commit()
    flash("Student deleted successfully.", "success")
    return redirect(url_for('admin_panel'))

@app.route("/admin-dashboard")
def admin_dashboard():
    students = Student.query.all()
    total_present = sum(1 for s in students if s.attended)
    total_absent = sum(1 for s in students if not s.attended)
    total_students = len(students)  # ✅ Total number of students

    sem_stats = {}
    for sem in range(1, 7):
        sem_present = sum(1 for s in students if s.semester == sem and s.attended)
        sem_absent = sum(1 for s in students if s.semester == sem and not s.attended)
        sem_stats[sem] = {"present": sem_present, "absent": sem_absent}

    return render_template(
        "admin_dashboard.html",
        total_present=total_present,
        total_absent=total_absent,
        total_students=total_students,  # ✅ Pass to template
        sem_stats=sem_stats
    )


@app.route('/chart_data')
def chart_data():
    # Safe CSV path (works on any OS)
    file_path = os.path.join(app.root_path, 'static', 'csv_exports', 'registrations.csv')

    # If file missing → return empty dataset
    if not os.path.exists(file_path):
        return jsonify({
            "labels": [],
            "values": [],
            "legend": "Students per Semester"
        })

    # Read CSV without headers (since you said no col names)
    df = pd.read_csv(file_path, header=None)

    # Semester is column index 2 (third column)
    semester_counts = df[2].value_counts().sort_index()

    labels = semester_counts.index.astype(str).tolist()
    values = semester_counts.values.tolist()

    return jsonify({
        "labels": labels,
        "values": values,
        "legend": "Students per Semester"
    })
    
@app.route('/dashboard_data')
def dashboard_data():
    # Count students in semesters 2–6
    paying_students = db.session.query(func.count(Student.id)) \
                                .filter(Student.semester != 1) \
                                .scalar() or 0

    total_amount = paying_students * 100

    return jsonify({"total_amount": total_amount})
    
# ----------------------------- Run -----------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
