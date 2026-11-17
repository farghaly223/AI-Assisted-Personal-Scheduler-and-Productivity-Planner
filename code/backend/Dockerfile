FROM python:3.15.2-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock

COPY app ./app
COPY prod-ca-2021.crt ./

# The host .env uses a Windows CA path; adapt only that path in container memory.
COPY <<'PY' /usr/local/bin/backend-entrypoint.py
import os
import sys
from pathlib import PureWindowsPath

from sqlalchemy.engine import make_url

for name in ("DATABASE_URL", "MIGRATION_DATABASE_URL"):
    value = os.environ.get(name)
    if value:
        url = make_url(value)
        certificate = url.query.get("sslrootcert")
        if certificate and PureWindowsPath(certificate).is_absolute():
            url = url.update_query_dict({"sslrootcert": "/app/prod-ca-2021.crt"})
            os.environ[name] = url.render_as_string(hide_password=False)

os.execvp(sys.argv[1], sys.argv[1:])
PY

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["python", "/usr/local/bin/backend-entrypoint.py"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
