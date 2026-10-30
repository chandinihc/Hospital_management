# CarePoint Hospital Management System

A Flask hospital-management demonstration. It uses SQLite locally and PostgreSQL on Vercel. It includes role-based sign-in, patient and doctor management, appointments, medical records, billing, pharmacy stock, laboratory requests, bed allocation, and stored-data reports. All data is fictional; payments are simulated and no payment details are collected.

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

## Deploy to Vercel

Vercel detects the root `app.py` Flask application automatically. Python 3.12
is selected by `.python-version`. Static CSS and JavaScript live under
`public/static/`, which Vercel serves through its CDN. Production data uses
PostgreSQL; the local SQLite database is not used by Vercel.

1. Push the project to GitHub yourself, then import
   `chandinihc/Hospital_management` at <https://vercel.com/new>.
2. In Vercel Marketplace, connect a Neon PostgreSQL database to the project.
   Ensure it provides the `DATABASE_URL` environment variable in Production
   and Preview.
3. Add these environment variables in Vercel (do not put their values in Git):
   `HMS_SECRET_KEY`, `HMS_ADMIN_PASSWORD`, `HMS_DOCTOR_PASSWORD`,
   `HMS_RECEPTION_PASSWORD`, and `HMS_PHARMACY_PASSWORD`. Use long, unique
   passwords of at least 16 characters, each using at least three character
   types; none should be `Demo@123`, and each account password must differ.
   Vercel automatically sets
   `VERCEL=1`, which requires the secret and PostgreSQL URL and enables secure
   cookies.
4. Deploy and open the deployment URL. The database schema and fictional
   sample records are created automatically when the PostgreSQL database is
   empty. Production account passwords are read only when accounts are first
   seeded.

### Optional: copy your local SQLite records

Vercel does not upload or persist the local SQLite file. The SQLite database
is left untouched by this project. To copy it to a new, empty Neon database,
first set `HMS_SKIP_DEMO_SEED=1` in the Vercel project settings and pull the
same setting locally. Configure `DATABASE_URL` locally with the Neon connection
string, and set `HMS_SECRET_KEY` locally to a private random value. Then run:

```powershell
$env:VERCEL = "1"
$env:HMS_SKIP_DEMO_SEED = "1"
flask --app app migrate-sqlite-to-postgres --source instance\hospital.db
```

The command refuses to write if any PostgreSQL application table already has
rows, locks the target tables for the duration of the transaction, copies IDs
and relationships, and never modifies the SQLite source. After verifying the
copied data, remove `HMS_SKIP_DEMO_SEED`
from Vercel; keep `DATABASE_URL` and all secrets set there. Back up the Neon
database regularly. Do not use real patient information unless the deployment
is configured and approved for applicable healthcare privacy requirements.

## Local demo accounts

These demo credentials are available for local development only. Production
requires the separate account passwords listed above.

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
