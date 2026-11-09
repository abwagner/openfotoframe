FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-release.txt requirements-cloud.txt requirements-billing.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt -r requirements-billing.txt \
    && useradd --uid 10001 --create-home frame \
    && mkdir -p /data/media && chown -R frame:frame /data
COPY release_app.py render_display.py artwork_catalog.py /app/
COPY scripts/release_init_schema.py scripts/release_worker.py scripts/release_migrate.py scripts/release_recovery.py scripts/release_upgrade_workers.py /app/scripts/
COPY release_migration/ /app/release_migration/
COPY release_workers/ /app/release_workers/
COPY deploy/release/postgres-rls.sql deploy/release/postgres-roles.sql /app/deploy/release/
COPY deploy/release/postgres-dispatch-role.sql /app/deploy/release/
COPY deploy/release/migrations/002_worker_dispatch.sql /app/deploy/release/migrations/
COPY release_backend/ /app/release_backend/
COPY release_billing/ /app/release_billing/
COPY release_delivery/ /app/release_delivery/
COPY release_web/ /app/release_web/
COPY templates/ /app/templates/
COPY static/ /app/static/
USER 10001:10001
ENV PYTHONUNBUFFERED=1 DATA_ROOT=/data MEDIA_ROOT=/data/media
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/server', timeout=3)"
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "/dev/null", "release_app:create_app()"]
