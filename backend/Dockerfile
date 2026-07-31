# Optional self-hosted/cloud deployment of the backend, as a plain FastAPI
# server -- NOT used by the desktop app, which spawns its own Python venv
# directly (see electron/main.cjs) and never touches this image. Set
# DATABASE_URL (e.g. postgresql://user:pass@host/db) to point this
# container at Postgres instead of the default local SQLite file -- see
# app/db.py.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8787

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
