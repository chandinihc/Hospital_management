from datetime import date, datetime, timedelta
from functools import wraps
from sqlite3 import IntegrityError

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from .db import get_db

bp = Blueprint("main", __name__)
ACCESS = {
    "Admin": {"dashboard", "patients", "doctors", "appointments", "records", "billing", "pharmacy", "laboratory", "beds", "reports"},
    "Doctor": {"dashboard", "patients", "appointments", "records", "laboratory", "reports"},
    "Receptionist": {"dashboard", "patients", "doctors", "appointments", "billing", "beds", "reports"},
    "Pharmacist": {"dashboard", "pharmacy"},
}
NAV = [
    ("dashboard", "Overview", "⌂"),
    ("patients", "Patients", "♡"),
    ("doctors", "Care team", "✚"),
    ("appointments", "Appointments", "▦"),
    ("records", "Medical records", "▤"),
    ("billing", "Billing", "＄"),
    ("pharmacy", "Pharmacy", "✚"),
    ("laboratory", "Laboratory", "⌕"),
    ("beds", "Beds & wards", "▥"),
    ("reports", "Reports", "▥"),
]


@bp.before_app_request
def load_user():
    user_id = session.get("user_id")
    g.user = get_db().execute("SELECT id,username,role FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None


def roles(*allowed):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.user:
                return redirect(url_for("main.login"))
            if g.user["role"] not in allowed:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate


def page(name, **context):
    role = g.user["role"]
    links = [(endpoint, label, icon) for endpoint, label, icon in NAV if endpoint in ACCESS[role]]
    return render_template(name, nav_links=links, **context)


def required(name, label, max_length=200):
    value = request.form.get(name, "").strip()
    if not value or len(value) > max_length:
        raise ValueError(f"{label} is required (maximum {max_length} characters).")
    return value


def positive_int(name, label, maximum=100000):
    try:
        value = int(request.form.get(name, ""))
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if value < 0 or value > maximum:
        raise ValueError(f"{label} must be between 0 and {maximum}.")
    return value


def money(name, label):
    try:
        value = round(float(request.form.get(name, "")), 2)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid amount.") from exc
    if value < 0 or value > 10000000:
        raise ValueError(f"{label} must be between 0 and 10,000,000.")
    return value


def valid_date(value, label):
    try:
        return date.fromisoformat(value).isoformat()
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be a valid date.") from exc


@bp.route("/")
def index():
    return redirect(url_for("main.dashboard") if g.user else url_for("main.login"))


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        user = get_db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("main.dashboard"))
        flash("The username or password was not recognized.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=("POST",))
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/dashboard")
@roles("Admin", "Doctor", "Receptionist", "Pharmacist")
def dashboard():
    db = get_db()
    if g.user["role"] == "Pharmacist":
        return redirect(url_for("main.pharmacy"))
    counts = {
        "patients": db.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
        "doctors": db.execute("SELECT COUNT(*) FROM doctors").fetchone()[0],
        "appointments": db.execute("SELECT COUNT(*) FROM appointments WHERE appointment_date>=? AND status='Upcoming'", (date.today().isoformat(),)).fetchone()[0],
        "beds": db.execute("SELECT COUNT(*) FROM beds WHERE status='Available'").fetchone()[0],
        "bills": db.execute("SELECT COUNT(*) FROM bills WHERE paid<total").fetchone()[0],
    }
    recent = db.execute(
        """SELECT a.*,p.name patient_name,p.patient_code,d.name doctor_name
           FROM appointments a JOIN patients p ON p.id=a.patient_id JOIN doctors d ON d.id=a.doctor_id
           ORDER BY a.appointment_date,a.appointment_time LIMIT 6"""
    ).fetchall()
    registrations = db.execute("SELECT * FROM patients ORDER BY registered_at DESC LIMIT 4").fetchall()
    alerts = db.execute("SELECT name,quantity,low_stock_threshold,expiry_date FROM medicines WHERE quantity<=low_stock_threshold OR expiry_date<=? ORDER BY quantity LIMIT 4", ((date.today()+timedelta(days=60)).isoformat(),)).fetchall() if g.user["role"] == "Admin" else []
    return page("dashboard.html", counts=counts, recent=recent, registrations=registrations, alerts=alerts)


@bp.route("/patients", methods=("GET", "POST"))
@roles("Admin", "Doctor", "Receptionist")
def patients():
    db = get_db()
    if request.method == "POST":
        try:
            name = required("name", "Patient name", 100)
            age = positive_int("age", "Age", 120)
            phone = required("phone", "Phone number", 30)
            address = required("address", "Address", 250)
            emergency = required("emergency_contact", "Emergency contact", 30)
            code = f"PT-{datetime.now().strftime('%y%m%d%H%M%S%f')}"
            db.execute(
                "INSERT INTO patients(patient_code,name,age,phone,email,address,emergency_contact) VALUES(?,?,?,?,?,?,?)",
                (code, name, age, phone, request.form.get("email", "").strip()[:120], address, emergency),
            )
            db.commit()
            flash(f"Patient {code} registered.", "success")
            return redirect(url_for("main.patients"))
        except ValueError as error:
            flash(str(error), "error")
    query = request.args.get("q", "").strip()
    rows = db.execute(
        "SELECT * FROM patients WHERE name LIKE ? OR patient_code LIKE ? OR phone LIKE ? ORDER BY registered_at DESC",
        (f"%{query}%", f"%{query}%", f"%{query}%"),
    ).fetchall()
    return page("patients.html", patients=rows, query=query)


@bp.route("/patients/<int:patient_id>")
@roles("Admin", "Doctor", "Receptionist")
def patient_detail(patient_id):
    db = get_db()
    patient = db.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
    if not patient:
        abort(404)
    records = []
    if g.user["role"] in ("Admin", "Doctor"):
        records = db.execute("SELECT r.*,d.name doctor_name FROM medical_records r JOIN doctors d ON d.id=r.doctor_id WHERE r.patient_id=? ORDER BY r.visit_date DESC", (patient_id,)).fetchall()
    return page("patient_detail.html", patient=patient, records=records)


@bp.route("/patients/<int:patient_id>/update", methods=("POST",))
@roles("Admin", "Receptionist")
def update_patient(patient_id):
    db = get_db()
    try:
        name = required("name", "Patient name", 100)
        age = positive_int("age", "Age", 120)
        phone = required("phone", "Phone number", 30)
        address = required("address", "Address", 250)
        emergency = required("emergency_contact", "Emergency contact", 30)
        updated = db.execute(
            """UPDATE patients SET name=?,age=?,phone=?,email=?,address=?,emergency_contact=?
               WHERE id=?""",
            (name, age, phone, request.form.get("email", "").strip()[:120],
             address, emergency, patient_id),
        )
        if updated.rowcount != 1:
            abort(404)
        db.commit()
        flash("Patient information updated.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("main.patient_detail", patient_id=patient_id))


@bp.route("/doctors", methods=("GET", "POST"))
@roles("Admin", "Receptionist")
def doctors():
    db = get_db()
    if request.method == "POST":
        try:
            values = tuple(required(field, label, limit) for field, label, limit in (
                ("name", "Doctor name", 100), ("specialization", "Specialization", 100),
                ("department", "Department", 100), ("phone", "Phone number", 30),
                ("availability", "Availability schedule", 120),
            ))
            db.execute("INSERT INTO doctors(name,specialization,department,phone,email,availability) VALUES(?,?,?,?,?,?)", (*values[:4], request.form.get("email", "").strip()[:120], values[4]))
            db.commit()
            flash("Doctor profile added.", "success")
            return redirect(url_for("main.doctors"))
        except ValueError as error:
            flash(str(error), "error")
    rows = db.execute("SELECT * FROM doctors ORDER BY department,name").fetchall()
    return page("doctors.html", doctors=rows)


@bp.route("/doctors/<int:doctor_id>/schedule", methods=("POST",))
@roles("Admin")
def update_doctor_schedule(doctor_id):
    db = get_db()
    try:
        schedule = required("availability", "Availability schedule", 120)
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("main.doctors"))
    updated = db.execute("UPDATE doctors SET availability=? WHERE id=?", (schedule, doctor_id))
    if updated.rowcount != 1:
        abort(404)
    db.commit()
    flash("Doctor availability updated.", "success")
    return redirect(url_for("main.doctors"))


@bp.route("/doctors/<int:doctor_id>/update", methods=("POST",))
@roles("Admin")
def update_doctor(doctor_id):
    db = get_db()
    try:
        values = tuple(required(field, label, limit) for field, label, limit in (
            ("name", "Doctor name", 100), ("specialization", "Specialization", 100),
            ("department", "Department", 100), ("phone", "Phone number", 30),
            ("availability", "Availability schedule", 120),
        ))
        updated = db.execute(
            """UPDATE doctors
               SET name=?,specialization=?,department=?,phone=?,email=?,availability=?
               WHERE id=?""",
            (*values[:4], request.form.get("email", "").strip()[:120], values[4], doctor_id),
        )
        if updated.rowcount != 1:
            abort(404)
        db.commit()
        flash("Doctor profile updated.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("main.doctors"))


@bp.route("/appointments", methods=("GET", "POST"))
@roles("Admin", "Doctor", "Receptionist")
def appointments():
    db = get_db()
    if request.method == "POST":
        try:
            patient_id = positive_int("patient_id", "Patient", 10000000)
            doctor_id = positive_int("doctor_id", "Doctor", 10000000)
            appt_date = valid_date(required("appointment_date", "Appointment date", 10), "Appointment date")
            appt_time = required("appointment_time", "Appointment time", 5)
            datetime.strptime(appt_time, "%H:%M")
            if not db.execute("SELECT id FROM patients WHERE id=?", (patient_id,)).fetchone():
                raise ValueError("Select an existing patient.")
            doctor = db.execute("SELECT department FROM doctors WHERE id=?", (doctor_id,)).fetchone()
            if not doctor:
                raise ValueError("Select an existing doctor.")
            if appt_date < date.today().isoformat():
                raise ValueError("Appointments cannot be booked in the past.")
            db.execute("INSERT INTO appointments(patient_id,doctor_id,appointment_date,appointment_time,department) VALUES(?,?,?,?,?)",
                       (patient_id, doctor_id, appt_date, appt_time, doctor["department"]))
            db.commit()
            flash("Appointment booked.", "success")
            return redirect(url_for("main.appointments"))
        except (ValueError, IntegrityError) as error:
            if isinstance(error, IntegrityError):
                db.rollback()
                flash("That doctor already has an appointment at this time.", "error")
            else:
                flash(str(error) or "Enter a valid appointment time (HH:MM).", "error")
    rows = db.execute("SELECT a.*,p.name patient_name,p.patient_code,d.name doctor_name FROM appointments a JOIN patients p ON p.id=a.patient_id JOIN doctors d ON d.id=a.doctor_id ORDER BY a.appointment_date DESC,a.appointment_time").fetchall()
    return page("appointments.html", appointments=rows, patients=db.execute("SELECT id,patient_code,name FROM patients ORDER BY name").fetchall(), doctors=db.execute("SELECT id,name,department FROM doctors ORDER BY name").fetchall())


@bp.route("/appointments/<int:appointment_id>/<action>", methods=("POST",))
@roles("Admin", "Doctor", "Receptionist")
def appointment_action(appointment_id, action):
    if action not in ("cancel", "complete"):
        abort(404)
    status = "Cancelled" if action == "cancel" else "Completed"
    db = get_db()
    updated = db.execute(
        "UPDATE appointments SET status=? WHERE id=? AND status='Upcoming'",
        (status, appointment_id),
    )
    if updated.rowcount != 1:
        db.rollback()
        flash("Appointment not found or no longer upcoming.", "error")
        return redirect(url_for("main.appointments"))
    db.commit()
    flash(f"Appointment marked {status.lower()}.", "success")
    return redirect(url_for("main.appointments"))


@bp.route("/appointments/<int:appointment_id>/reschedule", methods=("POST",))
@roles("Admin", "Doctor", "Receptionist")
def reschedule(appointment_id):
    db = get_db()
    try:
        appt_date = valid_date(required("appointment_date", "Appointment date", 10), "Appointment date")
        appt_time = required("appointment_time", "Appointment time", 5)
        datetime.strptime(appt_time, "%H:%M")
        if appt_date < date.today().isoformat():
            raise ValueError("Appointments cannot be moved into the past.")
        updated = db.execute(
            """UPDATE appointments SET appointment_date=?,appointment_time=?
               WHERE id=? AND status='Upcoming'""",
            (appt_date, appt_time, appointment_id),
        )
        if updated.rowcount != 1:
            db.rollback()
            flash("Appointment not found or no longer upcoming.", "error")
            return redirect(url_for("main.appointments"))
        db.commit()
        flash("Appointment rescheduled.", "success")
    except (ValueError, IntegrityError) as error:
        db.rollback()
        flash("That doctor already has an appointment at this time." if isinstance(error, IntegrityError) else str(error), "error")
    return redirect(url_for("main.appointments"))


@bp.route("/records", methods=("GET", "POST"))
@roles("Admin", "Doctor")
def records():
    db = get_db()
    if request.method == "POST":
        try:
            patient_id = positive_int("patient_id", "Patient", 10000000)
            doctor_id = positive_int("doctor_id", "Doctor", 10000000)
            visit = valid_date(required("visit_date", "Visit date", 10), "Visit date")
            symptoms = required("symptoms", "Symptoms", 1000)
            diagnosis = required("diagnosis", "Diagnosis", 1000)
            follow_up = request.form.get("follow_up", "").strip()
            if follow_up:
                follow_up = valid_date(follow_up, "Follow-up date")
                if follow_up < visit:
                    raise ValueError("Follow-up date cannot be before the visit date.")
            if not db.execute("SELECT id FROM patients WHERE id=?", (patient_id,)).fetchone() or not db.execute("SELECT id FROM doctors WHERE id=?", (doctor_id,)).fetchone():
                raise ValueError("Select an existing patient and doctor.")
            db.execute("INSERT INTO medical_records(patient_id,doctor_id,visit_date,symptoms,diagnosis,prescription,follow_up) VALUES(?,?,?,?,?,?,?)",
                       (patient_id, doctor_id, visit, symptoms, diagnosis, request.form.get("prescription", "").strip()[:1000], follow_up))
            db.commit()
            flash("Consultation record saved.", "success")
            return redirect(url_for("main.records"))
        except ValueError as error:
            flash(str(error), "error")
    rows = db.execute("SELECT r.*,p.name patient_name,p.patient_code,d.name doctor_name FROM medical_records r JOIN patients p ON p.id=r.patient_id JOIN doctors d ON d.id=r.doctor_id ORDER BY r.visit_date DESC,r.id DESC").fetchall()
    return page("records.html", records=rows, patients=db.execute("SELECT id,name,patient_code FROM patients ORDER BY name").fetchall(), doctors=db.execute("SELECT id,name FROM doctors ORDER BY name").fetchall())


@bp.route("/billing", methods=("GET", "POST"))
@roles("Admin", "Receptionist")
def billing():
    db = get_db()
    if request.method == "POST":
        try:
            patient_id = positive_int("patient_id", "Patient", 10000000)
            if not db.execute("SELECT id FROM patients WHERE id=?", (patient_id,)).fetchone():
                raise ValueError("Select an existing patient.")
            descriptions = request.form.getlist("description[]")
            amounts = request.form.getlist("amount[]")
            items = []
            for description, amount in zip(descriptions, amounts):
                if description.strip():
                    try:
                        value = round(float(amount), 2)
                    except ValueError as exc:
                        raise ValueError("Each bill item must have a valid amount.") from exc
                    if value < 0 or value > 10000000:
                        raise ValueError("Bill item amounts must be between 0 and 10,000,000.")
                    items.append((description.strip()[:120], value))
            if not items:
                raise ValueError("Add at least one item to the bill.")
            subtotal = round(sum(amount for _, amount in items), 2)
            discount = money("discount", "Discount")
            tax_rate = money("tax_rate", "Tax rate")
            if discount > subtotal or tax_rate > 100:
                raise ValueError("Discount cannot exceed subtotal and tax rate cannot exceed 100%.")
            tax = round((subtotal - discount) * tax_rate / 100, 2)
            total = round(subtotal - discount + tax, 2)
            paid = money("paid", "Initial payment")
            if paid > total:
                raise ValueError("Initial payment cannot exceed the bill total.")
            cur = db.execute("INSERT INTO bills(patient_id,subtotal,discount,tax,total,paid) VALUES(?,?,?,?,?,?)", (patient_id, subtotal, discount, tax, total, paid))
            db.executemany("INSERT INTO bill_items(bill_id,description,amount) VALUES(?,?,?)", [(cur.lastrowid, *item) for item in items])
            db.commit()
            flash(f"Invoice #{cur.lastrowid} generated.", "success")
            return redirect(url_for("main.billing"))
        except ValueError as error:
            flash(str(error), "error")
    rows = db.execute("SELECT b.*,p.name patient_name,p.patient_code FROM bills b JOIN patients p ON p.id=b.patient_id ORDER BY b.created_at DESC,b.id DESC").fetchall()
    items = {row["id"]: db.execute("SELECT * FROM bill_items WHERE bill_id=?", (row["id"],)).fetchall() for row in rows}
    return page("billing.html", bills=rows, bill_items=items, patients=db.execute("SELECT id,name,patient_code FROM patients ORDER BY name").fetchall())


@bp.route("/billing/<int:bill_id>/payment", methods=("POST",))
@roles("Admin", "Receptionist")
def payment(bill_id):
    db = get_db()
    bill = db.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
    if not bill:
        abort(404)
    try:
        amount = money("amount", "Payment")
        if amount <= 0 or bill["paid"] + amount > bill["total"]:
            raise ValueError("Payment must be greater than zero and cannot exceed the remaining balance.")
        db.execute("UPDATE bills SET paid=paid+? WHERE id=?", (amount, bill_id))
        db.commit()
        flash("Simulated payment recorded.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("main.billing"))


@bp.route("/pharmacy", methods=("GET", "POST"))
@roles("Admin", "Pharmacist")
def pharmacy():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            if action == "add":
                name = required("name", "Medicine name", 100)
                batch = required("batch_number", "Batch number", 40)
                quantity = positive_int("quantity", "Quantity")
                expiry = valid_date(required("expiry_date", "Expiry date", 10), "Expiry date")
                price = money("price", "Price")
                threshold = positive_int("low_stock_threshold", "Low-stock threshold", 100000)
                db.execute("INSERT INTO medicines(name,batch_number,quantity,expiry_date,price,low_stock_threshold) VALUES(?,?,?,?,?,?)", (name,batch,quantity,expiry,price,threshold))
            elif action == "stock":
                medicine_id = positive_int("medicine_id", "Medicine", 10000000)
                quantity = positive_int("quantity", "Quantity")
                if quantity == 0 or not db.execute("SELECT id FROM medicines WHERE id=?", (medicine_id,)).fetchone():
                    raise ValueError("Select a medicine and enter a positive stock quantity.")
                db.execute("UPDATE medicines SET quantity=quantity+? WHERE id=?", (quantity,medicine_id))
            elif action == "dispense":
                medicine_id = positive_int("medicine_id", "Medicine", 10000000)
                quantity = positive_int("quantity", "Quantity")
                medicine = db.execute("SELECT quantity FROM medicines WHERE id=?", (medicine_id,)).fetchone()
                if not medicine or quantity == 0:
                    raise ValueError("Select a medicine and enter a positive dispensing quantity.")
                if quantity > medicine["quantity"]:
                    raise ValueError("Dispensing quantity cannot exceed available stock.")
                db.execute("UPDATE medicines SET quantity=quantity-? WHERE id=?", (quantity,medicine_id))
            else:
                raise ValueError("Choose a valid inventory action.")
            db.commit()
            flash("Pharmacy inventory updated.", "success")
            return redirect(url_for("main.pharmacy"))
        except (ValueError, IntegrityError) as error:
            db.rollback()
            flash("That batch number already exists." if isinstance(error, IntegrityError) else str(error), "error")
    rows = db.execute("SELECT * FROM medicines ORDER BY expiry_date").fetchall()
    cutoff = (date.today() + timedelta(days=60)).isoformat()
    return page("pharmacy.html", medicines=rows, cutoff=cutoff)


@bp.route("/laboratory", methods=("GET", "POST"))
@roles("Admin", "Doctor")
def laboratory():
    db = get_db()
    if request.method == "POST":
        try:
            action = request.form.get("action")
            if action == "request":
                patient_id = positive_int("patient_id", "Patient", 10000000)
                test_name = required("test_name", "Test name", 120)
                if not db.execute("SELECT id FROM patients WHERE id=?", (patient_id,)).fetchone():
                    raise ValueError("Select an existing patient.")
                db.execute("INSERT INTO lab_tests(patient_id,test_name) VALUES(?,?)", (patient_id,test_name))
            elif action == "update":
                test_id = positive_int("test_id", "Test", 10000000)
                status = request.form.get("status", "")
                result = request.form.get("result", "").strip()[:1000]
                if status not in ("Pending", "In progress", "Completed"):
                    raise ValueError("Choose a valid test status.")
                if status == "Completed" and not result:
                    raise ValueError("A result is required to complete a test.")
                db.execute("UPDATE lab_tests SET status=?,result=? WHERE id=?", (status,result,test_id))
            else:
                raise ValueError("Choose a valid laboratory action.")
            db.commit()
            flash("Laboratory record updated.", "success")
            return redirect(url_for("main.laboratory"))
        except ValueError as error:
            flash(str(error), "error")
    rows = db.execute("SELECT l.*,p.name patient_name,p.patient_code FROM lab_tests l JOIN patients p ON p.id=l.patient_id ORDER BY l.requested_at DESC").fetchall()
    return page("laboratory.html", tests=rows, patients=db.execute("SELECT id,name,patient_code FROM patients ORDER BY name").fetchall())


@bp.route("/beds", methods=("GET", "POST"))
@roles("Admin", "Receptionist")
def beds():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        try:
            bed_id = positive_int("bed_id", "Bed", 10000000)
            bed = db.execute("SELECT * FROM beds WHERE id=?", (bed_id,)).fetchone()
            if not bed:
                raise ValueError("Select an existing bed.")
            if action == "admit":
                patient_id = positive_int("patient_id", "Patient", 10000000)
                if bed["status"] != "Available":
                    raise ValueError("Only available beds can be allocated.")
                if not db.execute("SELECT id FROM patients WHERE id=?", (patient_id,)).fetchone():
                    raise ValueError("Select an existing patient.")
                updated = db.execute("UPDATE beds SET status='Occupied',patient_id=? WHERE id=? AND status='Available'", (patient_id,bed_id))
                if updated.rowcount != 1:
                    raise ValueError("This bed was just allocated to another patient.")
            elif action == "discharge":
                if bed["status"] != "Occupied":
                    raise ValueError("This bed is not currently occupied.")
                db.execute("UPDATE beds SET status='Available',patient_id=NULL WHERE id=?", (bed_id,))
            else:
                raise ValueError("Choose a valid bed action.")
            db.commit()
            flash("Bed allocation updated.", "success")
            return redirect(url_for("main.beds"))
        except ValueError as error:
            db.rollback()
            flash(str(error), "error")
    rows = db.execute("SELECT b.*,p.name patient_name,p.patient_code FROM beds b LEFT JOIN patients p ON p.id=b.patient_id ORDER BY b.ward,b.room,b.bed_number").fetchall()
    return page("beds.html", beds=rows, patients=db.execute("SELECT id,name,patient_code FROM patients ORDER BY name").fetchall())


@bp.route("/reports")
@roles("Admin", "Doctor", "Receptionist")
def reports():
    db = get_db()
    start = request.args.get("start", (date.today() - timedelta(days=29)).isoformat())
    end = request.args.get("end", date.today().isoformat())
    try:
        valid_date(start, "Start date")
        valid_date(end, "End date")
        if start > end:
            raise ValueError("Start date must be on or before end date.")
    except ValueError as error:
        flash(str(error), "error")
        start, end = (date.today() - timedelta(days=29)).isoformat(), date.today().isoformat()
    appointments_count = db.execute("SELECT COUNT(*) FROM appointments WHERE appointment_date BETWEEN ? AND ?", (start,end)).fetchone()[0]
    daily_count = db.execute("SELECT COUNT(*) FROM appointments WHERE appointment_date=?", (date.today().isoformat(),)).fetchone()[0]
    monthly_count = db.execute("SELECT COUNT(*) FROM appointments WHERE strftime('%Y-%m',appointment_date)=?", (date.today().strftime("%Y-%m"),)).fetchone()[0]
    registrations_count = db.execute("SELECT COUNT(*) FROM patients WHERE date(registered_at) BETWEEN ? AND ?", (start,end)).fetchone()[0]
    revenue = db.execute("SELECT COALESCE(SUM(paid),0) paid,COALESCE(SUM(total-paid),0) outstanding FROM bills WHERE date(created_at) BETWEEN ? AND ?", (start,end)).fetchone()
    trend_rows = db.execute("SELECT appointment_date day,COUNT(*) count FROM appointments WHERE appointment_date BETWEEN ? AND ? GROUP BY appointment_date ORDER BY appointment_date", (start,end)).fetchall()
    max_count = max((row["count"] for row in trend_rows), default=1)
    trend = [{"day": row["day"], "count": row["count"], "height": max(12, round(row["count"] / max_count * 120))} for row in trend_rows]
    inventory = db.execute("SELECT COUNT(*) medicines,COALESCE(SUM(quantity),0) units FROM medicines").fetchone()
    admissions = db.execute("SELECT COUNT(*) FROM beds WHERE status='Occupied'").fetchone()[0]
    return page("reports.html", start=start, end=end, appointments_count=appointments_count, daily_count=daily_count, monthly_count=monthly_count, registrations_count=registrations_count, revenue=revenue, trend=trend, inventory=inventory, admissions=admissions)
