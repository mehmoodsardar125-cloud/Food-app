"""One command: builds the Docker image, starts the API container, creates the admin, starts the UI.

Usage:  python docker_run.py
Needs:  Docker Desktop running, and run.py + app.py + ui.py in this same folder.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from run import ensure_env, ensure_packages  # reuses the .env / packages logic from run.py

ROOT = Path(__file__).resolve().parent
IMAGE, CONTAINER, VOLUME = "food-app", "food-app-container", "foodeliver-data"
API_URL, UI_URL = "http://127.0.0.1:5000", "http://127.0.0.1:8501"

DOCKERFILE = """FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir Flask Flask-SQLAlchemy Flask-JWT-Extended Flask-Cors Flask-Limiter python-dotenv
COPY app.py .
ENV HOST=0.0.0.0
CMD ["python", "app.py"]
"""


def sh(*cmd, check=True):
    return subprocess.run(cmd, cwd=ROOT, check=check, capture_output=True, text=True)


def step(msg):
    print(f"\n==> {msg}")


def wait_for_api(seconds=40):
    for _ in range(seconds):
        try:
            with urllib.request.urlopen(API_URL, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def main():
    if not shutil.which("docker"):
        sys.exit("Docker not found. Install and start Docker Desktop first.")
    if sh("docker", "info", check=False).returncode != 0:
        sys.exit("Docker is installed but not running. Open Docker Desktop, wait until it is ready, then retry.")

    ensure_env()
    ensure_packages()  # streamlit + requests for the UI

    step("Writing Dockerfile")
    (ROOT / "Dockerfile").write_text(DOCKERFILE, encoding="ascii")

    step("Building image (first time takes a few minutes)")
    b = sh("docker", "build", "-t", IMAGE, ".", check=False)
    if b.returncode != 0:
        sys.exit("Docker build failed:\n" + b.stderr[-1500:])

    step("Starting API container")
    sh("docker", "rm", "-f", CONTAINER, check=False)
    r = sh("docker", "run", "-d", "-p", "5000:5000", "--name", CONTAINER,
           "-v", f"{VOLUME}:/app/instance",  # keeps the database when the container is removed
           "-e", f"JWT_SECRET_KEY={os.environ['JWT_SECRET_KEY']}",
           "-e", "DATABASE_URL=sqlite:////app/instance/foodeliver.db",
           "-e", "CORS_ORIGINS=http://127.0.0.1:8501,http://localhost:8501",
           IMAGE, check=False)
    if r.returncode != 0:
        sys.exit("Could not start container:\n" + r.stderr[-1500:] +
                 "\nIf port 5000 is busy, close the program using it and retry.")

    step("Waiting for API")
    if not wait_for_api():
        logs = sh("docker", "logs", CONTAINER, check=False)
        sys.exit("API did not respond. Container logs:\n" + (logs.stdout + logs.stderr)[-1500:])
    print("API is running:", API_URL, "(use 127.0.0.1, not the 172.x address)")

    step("Creating admin user")
    a = sh("docker", "exec", "-e", f"ADMIN_EMAIL={os.environ['ADMIN_EMAIL']}",
           "-e", f"ADMIN_PASSWORD={os.environ['ADMIN_PASSWORD']}", CONTAINER,
           "flask", "--app", "app", "create-admin", check=False)
    print("Admin created." if a.returncode == 0 else "Admin already exists (ok).")
    print(f"\nLogin ->  {os.environ['ADMIN_EMAIL']}  /  {os.environ['ADMIN_PASSWORD']}   (also saved in .env)")

    step(f"Starting UI at {UI_URL}  (press Ctrl+C to stop the UI; the API container keeps running)")
    ui = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "ui.py", "--server.headless", "true",
                           "--server.address", "127.0.0.1"], cwd=ROOT, env={**os.environ, "API_URL": API_URL})
    time.sleep(4)
    webbrowser.open(UI_URL)
    try:
        ui.wait()
    except KeyboardInterrupt:
        pass
    finally:
        ui.terminate()
        print("\nUI stopped. Stop the API with:  docker rm -f", CONTAINER)


if __name__ == "__main__":
    main()
