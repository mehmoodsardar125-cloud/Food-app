"""One-command launcher: installs packages, creates config, DB and admin, starts API + UI.

Usage:  python run.py              (start everything)
        python run.py --setup-only (just prepare, don't start servers)
"""
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
PACKAGES = {  # import name -> pip name
    "flask": "Flask", "flask_sqlalchemy": "Flask-SQLAlchemy", "flask_jwt_extended": "Flask-JWT-Extended",
    "flask_cors": "Flask-Cors", "flask_limiter": "Flask-Limiter", "dotenv": "python-dotenv",
    "streamlit": "streamlit", "requests": "requests",
}


def ensure_packages(db_url=""):
    need = dict(PACKAGES)
    if db_url.startswith("postgres"):
        need["psycopg2"] = "psycopg2-binary"
    missing = []
    for mod, pip_name in need.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print("Installing:", ", ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])


def ensure_env():
    """Create .env (and .gitignore so secrets are never committed) on first run."""
    if not ENV_FILE.exists():
        admin_pw = secrets.token_urlsafe(10)
        ENV_FILE.write_text(
            f"JWT_SECRET_KEY={secrets.token_urlsafe(48)}\n"
            "DATABASE_URL=sqlite:///foodeliver.db\n"  # use postgresql://user:pass@host/db for PostgreSQL
            "CORS_ORIGINS=http://localhost:8501\n"
            "ADMIN_EMAIL=admin@example.com\n"
            f"ADMIN_PASSWORD={admin_pw}\n"
        )
        print(f"Created .env  ->  admin login: admin@example.com / {admin_pw}")
    gi = ROOT / ".gitignore"
    if not gi.exists():
        gi.write_text(".env\n*.db\ninstance/\n__pycache__/\n")
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def setup_database():
    sys.path.insert(0, str(ROOT))
    from werkzeug.security import generate_password_hash
    import app as backend

    with backend.app.app_context():
        backend.db.create_all()
        email = os.environ["ADMIN_EMAIL"].lower()
        if not backend.User.query.filter_by(email=email).first():
            backend.db.session.add(backend.User(name="Admin", email=email, role="admin",
                                                password_hash=generate_password_hash(os.environ["ADMIN_PASSWORD"])))
            backend.db.session.commit()
            print("Admin user created:", email)


def main():
    ensure_env()
    ensure_packages(os.environ.get("DATABASE_URL", ""))
    setup_database()
    if "--setup-only" in sys.argv:
        print("Setup complete.")
        return
    print("\nAPI -> http://127.0.0.1:5000   UI -> http://localhost:8501   (Ctrl+C to stop)\n")
    api = subprocess.Popen([sys.executable, "-m", "flask", "--app", "app", "run"], cwd=ROOT)
    ui = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "ui.py", "--server.headless", "true"], cwd=ROOT)
    try:
        while api.poll() is None and ui.poll() is None:
            time.sleep(1)
        for name, p in (("API", api), ("UI", ui)):
            if p.poll() is not None:
                print(f"\n{name} stopped (exit code {p.returncode}). Scroll up in the terminal for the error message.")
    except KeyboardInterrupt:
        pass
    finally:
        for p in (api, ui):
            p.terminate()


if __name__ == "__main__":
    main()
