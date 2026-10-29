import os
import tempfile
import unittest
from datetime import date, timedelta

from hms import create_app
from hms.db import get_db


class HospitalManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temp_dir.name, "hospital.db")
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-key",
                "DATABASE": self.database,
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
