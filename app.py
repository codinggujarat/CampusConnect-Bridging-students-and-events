from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
import razorpay
import uuid
import qrcode
import csv
import os
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'default_secret')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///students.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Razorpay
RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET')
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# ----------------------------- Models -----------------------------
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unique_id = db.Column(db.String(100), unique=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(100))
    semester = db.Column(db.Integer)
    attended = db.Column(db.Boolean, default=False)

# ----------------------------- CSV Helpers -----------------------------
CSV_PATH = os.path.join('static', 'csv_exports', 'registrations.csv')
os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)

def append_to_csv(name, email, semester, unique_id, paid=False):
    file_exists = os.path.isfile(CSV_PATH)
    with open(CSV_PATH, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(['Name', 'Email', 'Semester', 'Event ID', 'Payment Status', 'Paid via', 'Amount', 'UPI ID', 'Attendance'])

        if semester in [2, 3, 4, 5, 6] or paid:
            writer.writerow([name, email, semester, unique_id, 'Confirmed', 'Razorpay', '₹100', 'yourupi@bankname', 'Not Marked'])
        else:
            writer.writerow([name, email, semester, unique_id, '-', '-', '-', '-', 'Not Marked'])

def update_attendance_in_csv(uid):
    if not os.path.isfile(CSV_PATH):
        return
    rows = []
    with open(CSV_PATH, 'r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)
        for row in reader:
            if row[3] == uid:  # Match on Event ID
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
    unique_id = f"MSCCAIT2025-{str(uuid.uuid4())[:8]}"

    if semester == 1:
        student = Student(name=name, email=email, semester=semester, unique_id=unique_id)
        db.session.add(student)
        db.session.commit()

        generate_qr_code(unique_id)
        generate_pdf(name, email, semester, unique_id)
        append_to_csv(name, email, semester, unique_id, paid=False)

        flash("Free registration successful!", "success")
        return redirect(url_for('download_all', uid=unique_id))
    else:
        session['registration'] = {
            'name': name,
            'email': email,
            'semester': semester,
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

    student = Student(**data)
    db.session.add(student)
    db.session.commit()
    session.pop('registration')

    generate_qr_code(student.unique_id)
    generate_pdf(student.name, student.email, student.semester, student.unique_id, paid=True)
    append_to_csv(student.name, student.email, student.semester, student.unique_id, paid=True)

    flash("Payment successful! Download your QR and PDF below.", "success")
    return redirect(url_for('download_all', uid=student.unique_id))

# ----------------------------- QR / PDF -----------------------------
def generate_qr_code(uid):
    folder = os.path.join('static', 'qr_codes')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f'{uid}.png')
    img = qrcode.make(f'{uid}')
    img.save(path)

def generate_pdf(name, email, semester, uid, paid=False):
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

    if semester in [2, 3, 4, 5, 6] or paid:
        flowables.append(Paragraph("✅ Payment Status: Confirmed", styles['Normal']))
        flowables.append(Paragraph("💳 Paid via: Razorpay", styles['Normal']))
        flowables.append(Paragraph("📌 Amount: ₹100", styles['Normal']))
        flowables.append(Paragraph("🆔 UPI ID: yourupi@bankname", styles['Normal']))
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
        return f"<h3>❌ Invalid QR or student not registered.</h3>"

    if student.attended:
        return f"<h3>⚠️ {student.name} has already attended.</h3>"

    student.attended = True
    db.session.commit()
    update_attendance_in_csv(uid)
    return f"<h3>✅ Attendance marked for {student.name} (Sem {student.semester}).</h3>"

# Scanner Manual Fallback
@app.route('/verify')
def verify_redirect():
    uid = request.args.get('uid')
    return redirect(url_for('verify', uid=uid))

# ----------------------------- Scanner Page -----------------------------
@app.route('/scanner')
def scanner():
    return render_template('scanner.html')  # HTML includes html5-qrcode

@app.route('/scan')
def scan_qr_page():
    return render_template('scan.html')  # HTML includes html5-qrcode

# ----------------------------- Run -----------------------------
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
