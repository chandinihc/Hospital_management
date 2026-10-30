import os
import re
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import click
from hms import create_app
from hms.db import (
    POSTGRES_SCHEMA,
    POSTGRES_SCHEMA_LOCK_ID,
    HybridRow,
    PostgreSQLConnection,
    get_db,
    get_production_passwords,
    init_db,
    migrate_sqlite_to_postgres_command,
    postgres_row_factory,
    seed_demo_data,
)
from werkzeug.security import check_password_hash


class FakeResult:
    def __init__(self, row=(1,)):
        self.row = row
        self.rowcount = 1

    def fetchone(self):
        return self.row


class FakePostgresConnection:
    def __init__(self, row_counts=None):
        self.row_counts = row_counts or {}
        self.statements = []
        self.transaction_active = False
        self.transaction_committed = False
        self.transaction_rolled_back = False

    def execute(self, query, parameters=()):
        self.statements.append((query, parameters, self.transaction_active))
        if query.startswith("SELECT COUNT(*) FROM "):
            table = query.rsplit(" ", 1)[-1]
            return FakeResult((self.row_counts.get(table, 0),))
        return FakeResult()

    def commit(self):
        self.transaction_committed = True

    def rollback(self):
        pass

    def close(self):
        pass

    @contextmanager
    def transaction(self):
        self.transaction_active = True
        try:
            yield
        except Exception:
            self.transaction_rolled_back = True
            raise
        else:
            self.transaction_committed = True
        finally:
            self.transaction_active = False


class HospitalManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temp_dir.name, "hospital.db")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": self.database,
                "WTF_CSRF_ENABLED": False,
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def login(self, username):
        return self.client.post(
            "/login",
            data={"username": username, "password": "Demo@123"},
            follow_redirects=True,
        )

    def test_login_with_csrf_redirects_to_dashboard(self):
        self.app.config["WTF_CSRF_ENABLED"] = True
        login_page = self.client.get("/login")
        html = login_page.get_data(as_text=True)
        tokens = re.findall(r'name="csrf_token" value="([^"]+)"', html)

        self.assertEqual(login_page.status_code, 200)
        self.assertEqual(len(tokens), 1)
        rejected = self.client.post(
            "/login", data={"username": "admin", "password": "Demo@123"}
        )
        self.assertEqual(rejected.status_code, 400)

        response = self.client.post(
            "/login",
            data={
                "username": "admin",
                "password": "Demo@123",
                "csrf_token": tokens[0],
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request.path, "/dashboard")
        self.assertIn("Good day, Admin", response.get_data(as_text=True))

    def test_health_check(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_static_assets_are_served_from_vercel_public_directory(self):
        css = self.client.get("/static/css/style.css")
        javascript = self.client.get("/static/js/app.js")

        self.assertEqual(css.status_code, 200)
        self.assertTrue(css.mimetype.startswith("text/css"))
        self.assertEqual(javascript.status_code, 200)
        self.assertIn("DOMContentLoaded", javascript.get_data(as_text=True))

    def test_vercel_requires_database_url(self):
        with patch.dict(os.environ, {"VERCEL": "1"}):
            os.environ.pop("DATABASE_URL", None)
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                create_app({"SECRET_KEY": "test-key"})

    def test_production_requires_database_url(self):
        with patch.dict(
            os.environ,
            {"FLASK_ENV": "production", "HMS_SECRET_KEY": "secret"},
        ):
            os.environ.pop("DATABASE_URL", None)
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                create_app()

    def test_production_requires_account_passwords_even_when_database_exists(self):
        strong_passwords = {
            "HMS_ADMIN_PASSWORD": "SecureAdminPass123!",
            "HMS_DOCTOR_PASSWORD": "SecureDoctorPass456!",
            "HMS_RECEPTION_PASSWORD": "SecureReception789!",
            "HMS_PHARMACY_PASSWORD": "SecurePharmacy012!",
        }
        with patch.dict(
            os.environ,
            {
                "FLASK_ENV": "production",
                "HMS_SECRET_KEY": "test-production-secret",
                "DATABASE_URL": "postgresql://test/database",
                **strong_passwords,
            },
        ):
            os.environ.pop("HMS_ADMIN_PASSWORD")
            with self.assertRaisesRegex(RuntimeError, "HMS_ADMIN_PASSWORD"):
                create_app()

    def test_vercel_startup_uses_postgresql_and_secure_cookies(self):
        fake_connection = FakePostgresConnection({"users": 1})
        with patch.dict(
            os.environ,
            {
                "VERCEL": "1",
                "HMS_SECRET_KEY": "test-production-secret",
                "DATABASE_URL": "postgresql://test/database",
                "HMS_ADMIN_PASSWORD": "SecureAdminPass123!",
                "HMS_DOCTOR_PASSWORD": "SecureDoctorPass456!",
                "HMS_RECEPTION_PASSWORD": "SecureReception789!",
                "HMS_PHARMACY_PASSWORD": "SecurePharmacy012!",
            },
        ):
            with patch(
                "hms.db.psycopg.connect", return_value=fake_connection
            ) as connect:
                vercel_app = create_app()

        self.assertTrue(vercel_app.config["SESSION_COOKIE_SECURE"])
        connect.assert_called_once()

    def test_postgresql_connection_uses_url_and_translates_parameters(self):
        fake_connection = Mock()
        with self.app.app_context():
            self.app.config["DATABASE_URL"] = "postgresql://user:pass@db/test"
            with patch("hms.db.psycopg.connect", return_value=fake_connection) as connect:
                database = get_db()

            self.assertIsInstance(database, PostgreSQLConnection)
            connect.assert_called_once_with(
                "postgresql://user:pass@db/test",
                row_factory=postgres_row_factory,
                connect_timeout=10,
            )
            database.execute("SELECT id FROM users WHERE id=?", (7,))
            fake_connection.execute.assert_called_with(
                "SELECT id FROM users WHERE id=%s", (7,)
            )
            database.executemany(
                "INSERT INTO users(id,username) VALUES(?,?)", [(1, "admin")]
            )
            fake_connection.cursor.return_value.executemany.assert_called_with(
                "INSERT INTO users(id,username) VALUES(%s,%s)", [(1, "admin")]
            )

        self.assertEqual(HybridRow((("id", 7),))["id"], 7)
        self.assertEqual(HybridRow((("id", 7),))[0], 7)

    def test_postgresql_row_factory_supports_named_and_indexed_columns(self):
        cursor = SimpleNamespace(
            description=[
                SimpleNamespace(name="id"),
                SimpleNamespace(name="username"),
            ]
        )
        row = postgres_row_factory(cursor)((7, "admin"))

        self.assertEqual(row["id"], 7)
        self.assertEqual(row[0], 7)
        self.assertEqual(row["username"], "admin")
        self.assertEqual(row[1], "admin")

    def test_postgresql_schema_lock_is_acquired_before_ddl(self):
        fake_connection = FakePostgresConnection({"users": 1})
        database = PostgreSQLConnection(fake_connection)
        with self.app.app_context():
            self.app.config["DATABASE_URL"] = "postgresql://test/database"
            with patch("hms.db.get_db", return_value=database):
                init_db()

        self.assertIn("pg_advisory_xact_lock", fake_connection.statements[0][0])
        self.assertEqual(
            fake_connection.statements[0][1], (POSTGRES_SCHEMA_LOCK_ID,)
        )
        self.assertIn("CREATE TABLE", fake_connection.statements[1][0])
        self.assertEqual(
            len(fake_connection.statements),
            len(POSTGRES_SCHEMA) + 2,
        )
        self.assertTrue(fake_connection.transaction_committed)

    def test_sqlite_migration_preserves_rows_and_orders_foreign_keys(self):
        passwords = {
            "HMS_ADMIN_PASSWORD": "SecureAdminPass123!",
            "HMS_DOCTOR_PASSWORD": "SecureDoctorPass456!",
            "HMS_RECEPTION_PASSWORD": "SecureReception789!",
            "HMS_PHARMACY_PASSWORD": "SecurePharmacy012!",
        }
        fake_connection = FakePostgresConnection()
        database = PostgreSQLConnection(fake_connection)
        with patch.dict(os.environ, passwords):
            with self.app.app_context():
                self.app.config["DATABASE_URL"] = "postgresql://test/database"
                with patch("hms.db.get_db", return_value=database):
                    migrate_sqlite_to_postgres_command.callback(self.database)

        self.assertTrue(fake_connection.transaction_committed)
        self.assertFalse(fake_connection.transaction_rolled_back)
        statements = [query for query, _, _ in fake_connection.statements]
        self.assertIn("pg_advisory_xact_lock", statements[0])
        insertion_tables = [
            query.split(" (", 1)[0].split()[-1]
            for query in statements
            if query.startswith("INSERT INTO ")
        ]
        self.assertEqual(
            insertion_tables,
            [
                "users",
                "users",
                "users",
                "users",
                "patients",
                "patients",
                "patients",
                "doctors",
                "doctors",
                "doctors",
                "appointments",
                "appointments",
                "medical_records",
                "bills",
                "bill_items",
                "medicines",
                "medicines",
                "medicines",
                "lab_tests",
                "beds",
                "beds",
                "beds",
                "beds",
            ],
        )
        self.assertEqual(
            len([query for query in statements if "setval(" in query]), 10
        )
        with self.app.app_context():
            self.app.config["DATABASE_URL"] = None
            self.assertEqual(
                get_db().execute("SELECT COUNT(*) FROM patients").fetchone()[0], 3
            )

    def test_sqlite_migration_rolls_back_if_postgres_tables_are_not_empty(self):
        passwords = {
            "HMS_ADMIN_PASSWORD": "SecureAdminPass123!",
            "HMS_DOCTOR_PASSWORD": "SecureDoctorPass456!",
            "HMS_RECEPTION_PASSWORD": "SecureReception789!",
            "HMS_PHARMACY_PASSWORD": "SecurePharmacy012!",
        }
        fake_connection = FakePostgresConnection({"patients": 1})
        database = PostgreSQLConnection(fake_connection)
        with patch.dict(os.environ, passwords):
            with self.app.app_context():
                self.app.config["DATABASE_URL"] = "postgresql://test/database"
                with patch("hms.db.get_db", return_value=database):
                    with self.assertRaisesRegex(click.ClickException, "not empty"):
                        migrate_sqlite_to_postgres_command.callback(self.database)

        self.assertTrue(fake_connection.transaction_rolled_back)
        self.assertFalse(
            any(
                query.startswith("INSERT INTO ")
                for query, _, _ in fake_connection.statements
            )
        )

    def test_production_requires_secret_key(self):
        with patch.dict(os.environ, {"FLASK_ENV": "production"}):
            os.environ.pop("HMS_SECRET_KEY", None)
            with self.assertRaisesRegex(
                RuntimeError, "HMS_SECRET_KEY must be set in production"
            ):
                create_app(
                    {
                        "DATABASE": os.path.join(
                            self.temp_dir.name, "missing-secret.db"
                        )
                    }
                )

    def test_production_requires_account_passwords(self):
        with patch.dict(os.environ, {}):
            for key in (
                "HMS_ADMIN_PASSWORD",
                "HMS_DOCTOR_PASSWORD",
                "HMS_RECEPTION_PASSWORD",
                "HMS_PHARMACY_PASSWORD",
            ):
                os.environ.pop(key, None)
            with self.assertRaisesRegex(ValueError, "HMS_ADMIN_PASSWORD"):
                get_production_passwords()

    def test_production_passwords_must_be_strong_unique_and_non_demo(self):
        passwords = {
            "HMS_ADMIN_PASSWORD": "SecureAdminPass123!",
            "HMS_DOCTOR_PASSWORD": "SecureDoctorPass456!",
            "HMS_RECEPTION_PASSWORD": "SecureReception789!",
            "HMS_PHARMACY_PASSWORD": "SecurePharmacy012!",
        }
        with patch.dict(os.environ, passwords):
            self.assertEqual(
                get_production_passwords()["admin"], passwords["HMS_ADMIN_PASSWORD"]
            )

        weak_passwords = passwords | {"HMS_ADMIN_PASSWORD": "short"}
        with patch.dict(os.environ, weak_passwords):
            with self.assertRaisesRegex(ValueError, "16 characters") as error:
                get_production_passwords()
            self.assertNotIn("short", str(error.exception))

        simple_passwords = passwords | {
            "HMS_ADMIN_PASSWORD": "averylonglowercasepassword"
        }
        with patch.dict(os.environ, simple_passwords):
            with self.assertRaisesRegex(ValueError, "three character types"):
                get_production_passwords()

        repeated_passwords = passwords | {
            "HMS_ADMIN_PASSWORD": passwords["HMS_DOCTOR_PASSWORD"]
        }
        with patch.dict(os.environ, repeated_passwords):
            with self.assertRaisesRegex(ValueError, "unique"):
                get_production_passwords()

        demo_passwords = passwords | {"HMS_ADMIN_PASSWORD": "Demo@123"}
        with patch.dict(os.environ, demo_passwords):
            with self.assertRaisesRegex(ValueError, "demo password"):
                get_production_passwords()

    def test_production_seeds_only_configured_account_passwords(self):
        passwords = {
            "HMS_ADMIN_PASSWORD": "SecureAdminPass123!",
            "HMS_DOCTOR_PASSWORD": "SecureDoctorPass456!",
            "HMS_RECEPTION_PASSWORD": "SecureReception789!",
            "HMS_PHARMACY_PASSWORD": "SecurePharmacy012!",
        }
        with patch.dict(os.environ, {"HMS_SKIP_DEMO_SEED": "1", **passwords}):
            production_app = create_app(
                {
                    "TESTING": True,
                    "SECRET_KEY": "test-key",
                    "DATABASE": os.path.join(self.temp_dir.name, "production.db"),
                }
            )
            with production_app.app_context():
                production_app.config["IS_PRODUCTION"] = True
                database = get_db()
                seed_demo_data(database)
                database.commit()
                users = get_db().execute(
                    "SELECT username,role,password_hash FROM users ORDER BY username"
                ).fetchall()
        self.assertEqual(
            [(user["username"], user["role"]) for user in users],
            [
                ("admin", "Admin"),
                ("doctor", "Doctor"),
                ("pharmacy", "Pharmacist"),
                ("reception", "Receptionist"),
            ],
        )
        self.assertTrue(
            check_password_hash(users[0]["password_hash"], passwords["HMS_ADMIN_PASSWORD"])
        )
        self.assertFalse(check_password_hash(users[0]["password_hash"], "Demo@123"))

        client = production_app.test_client()
        login_page = client.get("/login")
        token = re.search(
            r'name="csrf_token" value="([^"]+)"',
            login_page.get_data(as_text=True),
        ).group(1)
        response = client.post(
            "/login",
            data={
                "username": "admin",
                "password": passwords["HMS_ADMIN_PASSWORD"],
                "csrf_token": token,
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.request.path, "/dashboard")
        self.assertIn("Good day, Admin", response.get_data(as_text=True))

    def test_role_permissions_and_medical_record_confidentiality(self):
        response = self.client.get("/records")
        self.assertEqual(response.status_code, 302)

        self.login("reception")
        self.assertEqual(self.client.get("/pharmacy").status_code, 403)
        details = self.client.get("/patients/1").get_data(as_text=True)
        self.assertNotIn("Stable; routine monitoring advised", details)
        self.assertEqual(self.client.get("/doctors").status_code, 200)

        self.client.post("/logout")
        self.login("doctor")
        details = self.client.get("/patients/1").get_data(as_text=True)
        self.assertIn("Stable; routine monitoring advised", details)
        self.assertEqual(self.client.get("/billing").status_code, 403)

    def test_registration_validation_and_persistence_after_restart(self):
        self.login("reception")
        invalid = self.client.post(
            "/patients",
            data={"name": "Invalid Patient", "age": "121", "phone": "900",
                  "address": "Example address", "emergency_contact": "901"},
            follow_redirects=True,
        )
        self.assertIn("Age must be between 0 and 120", invalid.get_data(as_text=True))
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT COUNT(*) FROM patients").fetchone()[0], 3)

        self.client.post(
            "/patients",
            data={"name": "Test Patient", "age": "42", "phone": "9000000999",
                  "email": "test@example.test", "address": "Test street",
                  "emergency_contact": "9000000888"},
        )
        self.assertIn(
            "Aarav Sharma",
            self.client.get("/patients?q=aarav").get_data(as_text=True),
        )
        restarted = create_app(
            {"TESTING": True, "SECRET_KEY": "test-key", "DATABASE": self.database}
        )
        with restarted.app_context():
            patient = get_db().execute(
                "SELECT patient_code,name FROM patients WHERE name='Test Patient'"
            ).fetchone()
            self.assertIsNotNone(patient)
            self.assertTrue(patient["patient_code"].startswith("PT-"))

    def test_doctor_and_appointment_management(self):
        self.login("admin")
        self.client.post(
            "/doctors",
            data={"name": "Dr. Test", "specialization": "Pediatrics",
                  "department": "Pediatrics", "phone": "9000000777",
                  "availability": "Mon-Fri, 09:00-14:00"},
        )
        with self.app.app_context():
            doctor_id = get_db().execute(
                "SELECT id FROM doctors WHERE name='Dr. Test'"
            ).fetchone()["id"]

        booking = {
            "patient_id": "1",
            "doctor_id": str(doctor_id),
            "appointment_date": (date.today() + timedelta(days=1)).isoformat(),
            "appointment_time": "15:45",
        }
        self.assertEqual(self.client.post("/appointments", data=booking).status_code, 302)
        duplicate = self.client.post("/appointments", data=booking, follow_redirects=True)
        self.assertIn("already has an appointment", duplicate.get_data(as_text=True))
        self.assertEqual(self.client.get("/appointments").status_code, 200)

        with self.app.app_context():
            appointment_id = get_db().execute(
                "SELECT id FROM appointments WHERE doctor_id=? AND appointment_time='15:45'",
                (doctor_id,),
            ).fetchone()["id"]
        moved_date = (date.today() + timedelta(days=2)).isoformat()
        moved = self.client.post(
            f"/appointments/{appointment_id}/reschedule",
            data={"appointment_date": moved_date, "appointment_time": "16:00"},
            follow_redirects=True,
        )
        self.assertIn("Appointment rescheduled.", moved.get_data(as_text=True))
        with self.app.app_context():
            moved_appointment = get_db().execute(
                "SELECT appointment_date,appointment_time FROM appointments WHERE id=?",
                (appointment_id,),
            ).fetchone()
            self.assertEqual(tuple(moved_appointment), (moved_date, "16:00"))

        missing_action = self.client.post(
            "/appointments/999999/complete", follow_redirects=True
        )
        self.assertIn("Appointment not found or no longer upcoming.", missing_action.get_data(as_text=True))
        self.assertNotIn("Appointment marked completed.", missing_action.get_data(as_text=True))
        missing_reschedule = self.client.post(
            "/appointments/999999/reschedule",
            data={"appointment_date": moved_date, "appointment_time": "16:30"},
            follow_redirects=True,
        )
        self.assertIn("Appointment not found or no longer upcoming.", missing_reschedule.get_data(as_text=True))
        self.assertNotIn("Appointment rescheduled.", missing_reschedule.get_data(as_text=True))

        self.client.post(f"/appointments/{appointment_id}/cancel")
        inactive_action = self.client.post(
            f"/appointments/{appointment_id}/complete", follow_redirects=True
        )
        self.assertIn("Appointment not found or no longer upcoming.", inactive_action.get_data(as_text=True))
        inactive_reschedule = self.client.post(
            f"/appointments/{appointment_id}/reschedule",
            data={"appointment_date": moved_date, "appointment_time": "16:30"},
            follow_redirects=True,
        )
        self.assertIn("Appointment not found or no longer upcoming.", inactive_reschedule.get_data(as_text=True))
        replacement = self.client.post("/appointments", data=booking)
        self.assertEqual(replacement.status_code, 302)

    def test_patient_edits_and_admin_schedule_updates(self):
        self.login("reception")
        self.client.post(
            "/patients/1/update",
            data={"name": "Aarav Updated", "age": "35", "phone": "9000000111",
                  "email": "updated@example.test", "address": "New address",
                  "emergency_contact": "9000000222"},
        )
        self.client.post(
            "/doctors/1/schedule",
            data={"availability": "Not permitted"},
        )
        self.client.post("/logout")
        self.login("admin")
        self.client.post(
            "/doctors/1/schedule",
            data={"availability": "Wed-Fri, 12:00-17:00"},
        )
        with self.app.app_context():
            patient = get_db().execute("SELECT name,age FROM patients WHERE id=1").fetchone()
            doctor = get_db().execute("SELECT availability FROM doctors WHERE id=1").fetchone()
            self.assertEqual((patient["name"], patient["age"]), ("Aarav Updated", 35))
            self.assertEqual(doctor["availability"], "Wed-Fri, 12:00-17:00")

    def test_only_admin_can_update_doctor_profiles(self):
        self.login("reception")
        denied = self.client.post(
            "/doctors/1/update",
            data={"name": "Unauthorized Edit", "specialization": "Test",
                  "department": "Test", "phone": "9000000000",
                  "email": "", "availability": "Mon-Fri"},
        )
        self.assertEqual(denied.status_code, 403)
        self.client.post("/logout")
        self.login("admin")
        response = self.client.post(
            "/doctors/1/update",
            data={"name": "Dr. Updated Demo", "specialization": "Neurology",
                  "department": "Neurology", "phone": "9000000333",
                  "email": "updated-doctor@example.test",
                  "availability": "Wed-Fri, 12:00-17:00"},
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            doctor = get_db().execute(
                "SELECT name,specialization,department,phone,email,availability FROM doctors WHERE id=1"
            ).fetchone()
            self.assertEqual(tuple(doctor), (
                "Dr. Updated Demo", "Neurology", "Neurology", "9000000333",
                "updated-doctor@example.test", "Wed-Fri, 12:00-17:00",
            ))

    def test_records_billing_inventory_lab_and_beds(self):
        self.login("admin")
        self.client.post(
            "/records",
            data={"patient_id": "2", "doctor_id": "2",
                  "visit_date": date.today().isoformat(),
                  "symptoms": "Fictional symptom", "diagnosis": "Demo diagnosis",
                  "prescription": "Demo prescription"},
        )
        record = self.client.get("/records").get_data(as_text=True)
        self.assertIn("Demo diagnosis", record)

        self.client.post(
            "/billing",
            data={"patient_id": "2", "description[]": ["Consultation", "Test"],
                  "amount[]": ["1000", "500"], "discount": "100",
                  "tax_rate": "5", "paid": "100"},
        )
        with self.app.app_context():
            bill = get_db().execute(
                "SELECT * FROM bills WHERE patient_id=2 ORDER BY id DESC"
            ).fetchone()
            self.assertEqual(bill["total"], 1470.0)
        self.client.post(f"/billing/{bill['id']}/payment", data={"amount": "200"})
        with self.app.app_context():
            self.assertEqual(
                get_db().execute("SELECT paid FROM bills WHERE id=?", (bill["id"],)).fetchone()["paid"],
                300.0,
            )

        self.client.post("/pharmacy", data={"action": "dispense", "medicine_id": "1", "quantity": "200"}, follow_redirects=True)
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT quantity FROM medicines WHERE id=1").fetchone()["quantity"], 120)
        self.client.post("/pharmacy", data={"action": "dispense", "medicine_id": "1", "quantity": "4"})
        with self.app.app_context():
            self.assertEqual(get_db().execute("SELECT quantity FROM medicines WHERE id=1").fetchone()["quantity"], 116)

        self.client.post(
            "/laboratory",
            data={"action": "request", "patient_id": "2", "test_name": "Demo test"},
        )
        with self.app.app_context():
            test_id = get_db().execute(
                "SELECT id FROM lab_tests WHERE test_name='Demo test'"
            ).fetchone()["id"]
        result = self.client.post(
            "/laboratory",
            data={"action": "update", "test_id": str(test_id),
                  "status": "Completed", "result": "Fictional normal result"},
            follow_redirects=True,
        )
        self.assertIn("Fictional normal result", result.get_data(as_text=True))

        self.client.post(
            "/beds",
            data={"action": "admit", "bed_id": "1", "patient_id": "1"},
        )
        collision = self.client.post(
            "/beds",
            data={"action": "admit", "bed_id": "1", "patient_id": "2"},
            follow_redirects=True,
        )
        self.assertIn("Only available beds", collision.get_data(as_text=True))
        with self.app.app_context():
            bed = get_db().execute("SELECT status,patient_id FROM beds WHERE id=1").fetchone()
            self.assertEqual((bed["status"], bed["patient_id"]), ("Occupied", 1))

    def test_all_authorized_pages_render(self):
        self.login("admin")
        for path in (
            "/dashboard", "/patients", "/doctors", "/appointments", "/records",
            "/billing", "/pharmacy", "/laboratory", "/beds", "/reports",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)


if __name__ == "__main__":
    unittest.main()
