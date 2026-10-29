import sqlite3
from datetime import date, timedelta

import click
from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_error=None):
    database = g.pop("db", None)
    if database is not None:
        database.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, role TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS patients (
            id INTEGER PRIMARY KEY, patient_code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, age INTEGER NOT NULL CHECK(age BETWEEN 0 AND 120),
            phone TEXT NOT NULL, email TEXT DEFAULT '', address TEXT NOT NULL,
            emergency_contact TEXT NOT NULL, registered_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, specialization TEXT NOT NULL,
            department TEXT NOT NULL, phone TEXT NOT NULL, email TEXT DEFAULT '',
            availability TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
            doctor_id INTEGER NOT NULL REFERENCES doctors(id),
            appointment_date TEXT NOT NULL, appointment_time TEXT NOT NULL,
            department TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Upcoming'
                CHECK(status IN ('Upcoming','Completed','Cancelled')),
            notes TEXT DEFAULT ''
        );
        CREATE UNIQUE INDEX IF NOT EXISTS unique_active_appointment_slot
            ON appointments(doctor_id, appointment_date, appointment_time)
            WHERE status != 'Cancelled';
        CREATE TABLE IF NOT EXISTS medical_records (
            id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
            doctor_id INTEGER NOT NULL REFERENCES doctors(id), visit_date TEXT NOT NULL,
            symptoms TEXT NOT NULL, diagnosis TEXT NOT NULL, prescription TEXT DEFAULT '',
            follow_up TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
            created_at TEXT NOT NULL DEFAULT (datetime('now')), subtotal REAL NOT NULL,
            discount REAL NOT NULL DEFAULT 0, tax REAL NOT NULL DEFAULT 0,
            total REAL NOT NULL, paid REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS bill_items (
            id INTEGER PRIMARY KEY, bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
            description TEXT NOT NULL, amount REAL NOT NULL CHECK(amount >= 0)
        );
        CREATE TABLE IF NOT EXISTS medicines (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, batch_number TEXT NOT NULL UNIQUE,
            quantity INTEGER NOT NULL CHECK(quantity >= 0), expiry_date TEXT NOT NULL,
            price REAL NOT NULL CHECK(price >= 0), low_stock_threshold INTEGER NOT NULL DEFAULT 10
        );
        CREATE TABLE IF NOT EXISTS lab_tests (
            id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
            test_name TEXT NOT NULL, requested_at TEXT NOT NULL DEFAULT (datetime('now')),
            status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending','In progress','Completed')),
            result TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS beds (
            id INTEGER PRIMARY KEY, ward TEXT NOT NULL, room TEXT NOT NULL, bed_number TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'Available' CHECK(status IN ('Available','Occupied','Maintenance')),
            patient_id INTEGER REFERENCES patients(id)
        );
        """
    )
    if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        seed_demo_data(db)
    db.commit()


def seed_demo_data(db):
    users = [
        ("admin", "Admin"),
        ("doctor", "Doctor"),
        ("reception", "Receptionist"),
        ("pharmacy", "Pharmacist"),
    ]
    db.executemany(
        "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
        [(name, generate_password_hash("Demo@123"), role) for name, role in users],
    )
    patients = [
        ("PT-1001", "Aarav Sharma", 34, "9000000101", "aarav@example.test", "12 Lake Road", "9000000191"),
        ("PT-1002", "Mira Rao", 27, "9000000102", "mira@example.test", "8 Garden Street", "9000000192"),
        ("PT-1003", "Kabir Nair", 51, "9000000103", "kabir@example.test", "21 Hill View", "9000000193"),
    ]
    db.executemany(
        "INSERT INTO patients(patient_code,name,age,phone,email,address,emergency_contact) VALUES(?,?,?,?,?,?,?)",
        patients,
    )
    doctors = [
        ("Dr. Ananya Iyer", "Cardiology", "Cardiology", "9000000201", "ananya@example.test", "Mon-Fri, 09:00-14:00"),
        ("Dr. Rohan Das", "Orthopedics", "Orthopedics", "9000000202", "rohan@example.test", "Mon-Sat, 10:00-16:00"),
        ("Dr. Sana Khan", "General Medicine", "General Medicine", "9000000203", "sana@example.test", "Tue-Sat, 08:00-13:00"),
    ]
    db.executemany(
        "INSERT INTO doctors(name,specialization,department,phone,email,availability) VALUES(?,?,?,?,?,?)",
        doctors,
    )
    today = date.today()
    db.executemany(
        "INSERT INTO appointments(patient_id,doctor_id,appointment_date,appointment_time,department,status) VALUES(?,?,?,?,?,?)",
        [
            (1, 1, today.isoformat(), "10:00", "Cardiology", "Upcoming"),
            (2, 2, (today + timedelta(days=1)).isoformat(), "11:30", "Orthopedics", "Upcoming"),
        ],
    )
    db.execute(
        "INSERT INTO medical_records(patient_id,doctor_id,visit_date,symptoms,diagnosis,prescription,follow_up) VALUES(1,1,?,?,?,?,?)",
        (today.isoformat(), "Routine check-up", "Stable; routine monitoring advised", "Continue current care plan", (today + timedelta(days=30)).isoformat()),
    )
    db.execute(
        "INSERT INTO bills(patient_id,subtotal,discount,tax,total,paid) VALUES(1,1200,0,60,1260,500)"
    )
    db.execute("INSERT INTO bill_items(bill_id,description,amount) VALUES(1,'Consultation',1200)")
    db.executemany(
        "INSERT INTO medicines(name,batch_number,quantity,expiry_date,price,low_stock_threshold) VALUES(?,?,?,?,?,?)",
        [
            ("Paracetamol 500 mg", "DEMO-PAR-01", 120, (today + timedelta(days=500)).isoformat(), 2.5, 20),
            ("Amoxicillin 250 mg", "DEMO-AMX-02", 8, (today + timedelta(days=45)).isoformat(), 6.0, 15),
            ("Vitamin C 500 mg", "DEMO-VIT-03", 64, (today + timedelta(days=280)).isoformat(), 3.5, 10),
        ],
    )
    db.execute("INSERT INTO lab_tests(patient_id,test_name,status) VALUES(1,'Complete blood count','Pending')")
    db.executemany(
        "INSERT INTO beds(ward,room,bed_number,status) VALUES(?,?,?,?)",
        [("North", "101", "N-101-A", "Available"), ("North", "101", "N-101-B", "Available"),
         ("South", "202", "S-202-A", "Maintenance"), ("South", "203", "S-203-A", "Available")],
    )


def init_app(app):
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    with app.app_context():
        init_db()


@click.command("init-db")
def init_db_command():
    init_db()
    click.echo("Initialized the hospital database.")
