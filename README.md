# CarePoint Hospital Management System

A Flask and SQLite college-demonstration HMS. It includes role-based sign-in, patient and doctor management, appointments, medical records, billing, pharmacy stock, laboratory requests, bed allocation, and stored-data reports. All data is fictional; payments are simulated and no payment details are collected.

## Run locally

1. Install Python 3.10 or later.
2. On Windows, double-click `start_hms.bat`. It creates the virtual environment if needed, installs Flask when missing, and starts the server. Keep the console window open while using the app; close it to stop the server. Open http://127.0.0.1:5000 in your browser.

   Or start it from PowerShell:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python app.py
   ```

The SQLite database is created at `instance/hospital.db` on first run. Demo records and accounts are inserted only when the database is empty. Set `HMS_SECRET_KEY` to a long random value outside local demonstrations.

## Demo accounts

All demo accounts use the password `Demo@123`.

| Role | Username |
|---|---|
| Admin | `admin` |
| Doctor | `doctor` |
| Receptionist | `reception` |
| Pharmacist | `pharmacy` |

## Roles

- **Admin:** all modules and administration.
- **Doctor:** patients, appointments, medical records, laboratory, and reports.
- **Receptionist:** patients, doctors, appointments, billing, beds, and reports.
- **Pharmacist:** pharmacy inventory only.

The application validates inputs server-side, uses parameterized SQL, hashes passwords with Werkzeug, and enforces unique doctor/time appointment slots and occupied-bed constraints. This is a demonstration project, not a production clinical system.

## Run tests

```powershell
python -m unittest discover -s tests -v
```
