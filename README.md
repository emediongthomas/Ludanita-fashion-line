# Ludanita Fashion Line web tracker

This is the staff web version of the terminal order tracker. It provides
individual staff logins, shared order/payment records, and pages that refresh
every 30 seconds to show updates from other staff.

## Run locally on Windows

1. Create a virtual environment and install the project dependencies:

   ```powershell
   & "C:\Users\emediong\AppData\Local\Programs\Python\Python312\python.exe" -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

2. Start the local web app:

   ```powershell
   .\start_web.bat
   ```

   The script creates the local database and imports the current CSV records.
   The import skips IDs already present, so it is safe to start the app again.

3. Open <http://127.0.0.1:5000> in a browser.

4. In a second terminal, create the first administrator login:

   ```powershell
   .\.venv\Scripts\python.exe -m flask --app app create-user --admin Ludanita
   ```

   The command securely prompts for a password. Administrators can create,
   edit, and deactivate staff logins from the Staff page. To create another
   administrator from the command line, pass `--admin` with its username.

The local version stores records in `ludanita.sqlite3`. Keep the original CSV
files as a backup. After migrating, use the web app rather than the old
terminal app (`run.bat`): the old app still writes to CSV, which does not
synchronize changes back into the web database.

## Deploy for shared use

The local Flask server is for development on this computer only. For staff to
use the app from different devices or locations, deploy it to a Python web
host and attach a persistent PostgreSQL database:

- Set `DATABASE_URL` to the host's PostgreSQL connection URL.
- Set `SECRET_KEY` to a unique, long random value.
- Set `APP_ENV=production` so session cookies are marked secure.
- Use the `Procfile` start command (`gunicorn app:app`).
- Run `flask --app app init-db` once against the production database.
- Create staff accounts using `flask --app app create-user <username>`.
- Transfer the CSV files securely, then run `flask --app app import-csv` once
  against the production database.

Do not use the development server or SQLite as the live multi-staff service.
Use the hosting provider's HTTPS, persistent database, and backup features.
