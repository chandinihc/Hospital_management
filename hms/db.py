import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import click
import psycopg
from flask import current_app, g
from werkzeug.security import generate_password_hash


class HybridRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            key = tuple(self.keys())[key]
        return super().__getitem__(key)


def postgres_row_factory(cursor):
    columns = tuple(column.name for column in cursor.description)

    def make_row(values):
        return HybridRow(zip(columns, values))

    return make_row


class PostgreSQLConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query, parameters=()):
        return self.connection.execute(query.replace("?", "%s"), parameters)

    def executemany(self, query, parameters):
        return self.connection.cursor().executemany(
            query.replace("?", "%s"), parameters
        )

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg.IntegrityError)
POSTGRES_SCHEMA_LOCK_ID = 74829163
PRODUCTION_USERS = (
    ("admin", "Admin", "HMS_ADMIN_PASSWORD"),
    ("doctor", "Doctor", "HMS_DOCTOR_PASSWORD"),
    ("reception", "Receptionist", "HMS_RECEPTION_PASSWORD"),
    ("pharmacy", "Pharmacist", "HMS_PHARMACY_PASSWORD"),
)
MIN_PRODUCTION_PASSWORD_LENGTH = 16


def get_production_passwords():
    passwords = {
        username: os.environ.get(password_key, "")
        for username, _, password_key in PRODUCTION_USERS
    }
    missing = [
        password_key
        for username, _, password_key in PRODUCTION_USERS
        if not passwords[username]
    ]
    if missing:
        raise ValueError(
            "Set these environment variables before initializing the "
            f"production database: {', '.join(missing)}."
        )
    if "Demo@123" in passwords.values():
        raise ValueError(
            "Production account passwords must not use the demo password."
        )
    if any(
        len(password) < MIN_PRODUCTION_PASSWORD_LENGTH
        for password in passwords.values()
    ):
        raise ValueError(
            "Production account passwords must each be at least "
            f"{MIN_PRODUCTION_PASSWORD_LENGTH} characters long."
        )
    if any(
        sum(
            (
                any(character.islower() for character in password),
                any(character.isupper() for character in password),
                any(character.isdigit() for character in password),
                any(not character.isalnum() for character in password),
            )
        )
        < 3
        for password in passwords.values()
    ):
        raise ValueError(
            "Production account passwords must each use at least "
            "three character types."
        )
    if len(set(passwords.values())) != len(passwords):
        raise ValueError("Production account passwords must all be unique.")
    return passwords


def get_db():
    if "db" not in g:
        database_url = current_app.config.get("DATABASE_URL")
        if database_url:
            connection = psycopg.connect(
                database_url,
                row_factory=postgres_row_factory,
                connect_timeout=10,
            )
            g.db = PostgreSQLConnection(connection)
        else:
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
    if current_app.config.get("DATABASE_URL"):
        db.execute(
            "SELECT pg_advisory_xact_lock(?)", (POSTGRES_SCHEMA_LOCK_ID,)
        )
        for statement in POSTGRES_SCHEMA:
            db.execute(statement)
    else:
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
    if (
        db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        and os.environ.get("HMS_SKIP_DEMO_SEED") != "1"
    ):
        seed_demo_data(db)
    db.commit()


POSTGRES_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS users (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS patients (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        patient_code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
        age INTEGER NOT NULL CHECK(age BETWEEN 0 AND 120), phone TEXT NOT NULL,
        email TEXT DEFAULT '', address TEXT NOT NULL, emergency_contact TEXT NOT NULL,
        registered_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
    )""",
    """CREATE TABLE IF NOT EXISTS doctors (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL, specialization TEXT NOT NULL, department TEXT NOT NULL,
        phone TEXT NOT NULL, email TEXT DEFAULT '', availability TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS appointments (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        patient_id BIGINT NOT NULL REFERENCES patients(id),
        doctor_id BIGINT NOT NULL REFERENCES doctors(id),
        appointment_date TEXT NOT NULL, appointment_time TEXT NOT NULL,
        department TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'Upcoming'
            CHECK(status IN ('Upcoming','Completed','Cancelled')),
        notes TEXT DEFAULT ''
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS unique_active_appointment_slot
        ON appointments(doctor_id, appointment_date, appointment_time)
        WHERE status != 'Cancelled'""",
    """CREATE TABLE IF NOT EXISTS medical_records (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        patient_id BIGINT NOT NULL REFERENCES patients(id),
        doctor_id BIGINT NOT NULL REFERENCES doctors(id), visit_date TEXT NOT NULL,
        symptoms TEXT NOT NULL, diagnosis TEXT NOT NULL, prescription TEXT DEFAULT '',
        follow_up TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS bills (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        patient_id BIGINT NOT NULL REFERENCES patients(id),
        created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
        subtotal DOUBLE PRECISION NOT NULL, discount DOUBLE PRECISION NOT NULL DEFAULT 0,
        tax DOUBLE PRECISION NOT NULL DEFAULT 0, total DOUBLE PRECISION NOT NULL,
        paid DOUBLE PRECISION NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS bill_items (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        bill_id BIGINT NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
        description TEXT NOT NULL, amount DOUBLE PRECISION NOT NULL CHECK(amount >= 0)
    )""",
    """CREATE TABLE IF NOT EXISTS medicines (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        name TEXT NOT NULL, batch_number TEXT NOT NULL UNIQUE,
        quantity INTEGER NOT NULL CHECK(quantity >= 0), expiry_date TEXT NOT NULL,
        price DOUBLE PRECISION NOT NULL CHECK(price >= 0),
        low_stock_threshold INTEGER NOT NULL DEFAULT 10
    )""",
    """CREATE TABLE IF NOT EXISTS lab_tests (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        patient_id BIGINT NOT NULL REFERENCES patients(id), test_name TEXT NOT NULL,
        requested_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
        status TEXT NOT NULL DEFAULT 'Pending'
            CHECK(status IN ('Pending','In progress','Completed')),
        result TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS beds (
        id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
        ward TEXT NOT NULL, room TEXT NOT NULL, bed_number TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'Available'
            CHECK(status IN ('Available','Occupied','Maintenance')),
        patient_id BIGINT REFERENCES patients(id)
    )""",
)


def seed_demo_data(db):
    if current_app.config["IS_PRODUCTION"]:
        try:
            passwords = get_production_passwords()
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        users = [
            (username, role, passwords[username])
            for username, role, _ in PRODUCTION_USERS
        ]
    else:
        users = [
            ("admin", "Admin", "Demo@123"),
            ("doctor", "Doctor", "Demo@123"),
            ("reception", "Receptionist", "Demo@123"),
            ("pharmacy", "Pharmacist", "Demo@123"),
        ]
    db.executemany(
        "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
        [
            (name, generate_password_hash(password), role)
            for name, role, password in users
        ],
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
    app.cli.add_command(migrate_sqlite_to_postgres_command)
    with app.app_context():
        init_db()


@click.command("init-db")
def init_db_command():
    init_db()
    click.echo("Initialized the hospital database.")


@click.command("migrate-sqlite-to-postgres")
@click.option(
    "--source",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=True,
    help="Path to the SQLite database to copy. The source is never modified.",
)
def migrate_sqlite_to_postgres_command(source):
    if not current_app.config.get("DATABASE_URL"):
        raise click.ClickException(
            "Set DATABASE_URL to the empty PostgreSQL database before migrating."
        )

    tables = (
        "users",
        "patients",
        "doctors",
        "appointments",
        "medical_records",
        "bills",
        "bill_items",
        "medicines",
        "lab_tests",
        "beds",
    )
    source_uri = f"{Path(source).resolve().as_uri()}?mode=ro"
    source_db = sqlite3.connect(source_uri, uri=True)
    source_db.row_factory = sqlite3.Row
    try:
        target_db = get_db()
        try:
            production_passwords = get_production_passwords()
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        source_usernames = {
            row["username"]
            for row in source_db.execute("SELECT username FROM users")
        }
        missing_accounts = sorted(set(production_passwords) - source_usernames)
        if missing_accounts:
            raise click.ClickException(
                "SQLite source is missing the standard accounts required for "
                "production: " + ", ".join(missing_accounts)
            )

        with target_db.connection.transaction():
            target_db.execute(
                "SELECT pg_advisory_xact_lock(?)", (POSTGRES_SCHEMA_LOCK_ID,)
            )
            target_db.execute(
                f"LOCK TABLE {', '.join(tables)} IN ACCESS EXCLUSIVE MODE"
            )
            for table in tables:
                if target_db.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]:
                    raise click.ClickException(
                        f"PostgreSQL table {table!r} is not empty; migration "
                        "stopped without changing either database."
                    )

            migrated = {}
            for table in tables:
                rows = source_db.execute(f"SELECT * FROM {table}").fetchall()
                if not rows:
                    migrated[table] = 0
                    continue
                columns = tuple(rows[0].keys())
                column_sql = ", ".join(columns)
                placeholders = ", ".join("%s" for _ in columns)
                for row in rows:
                    target_db.execute(
                        f"INSERT INTO {table} ({column_sql}) "
                        f"OVERRIDING SYSTEM VALUE VALUES ({placeholders})",
                        tuple(row[column] for column in columns),
                    )
                target_db.execute(
                    "SELECT setval(pg_get_serial_sequence(?, 'id'), "
                    f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}",
                    (table,),
                )
                migrated[table] = len(rows)
            for username, password in production_passwords.items():
                target_db.execute(
                    "UPDATE users SET password_hash=? WHERE username=?",
                    (generate_password_hash(password), username),
                )
    finally:
        source_db.close()

    click.echo("SQLite data copied to PostgreSQL without modifying the source:")
    for table, count in migrated.items():
        click.echo(f"  {table}: {count} rows")
