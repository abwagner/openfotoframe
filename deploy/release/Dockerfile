FROM python:3.13-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-release.txt requirements-cloud.txt requirements-billing.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt -r requirements-billing.txt \
    && useradd --uid 10001 --create-home frame \
    && mkdir -p /data/media && chown -R frame:frame /data
COPY --chown=frame:frame release_app.py render_display.py artwork_catalog.py /app/
COPY --chown=frame:frame scripts/release_init_schema.py scripts/release_worker.py scripts/release_migrate.py scripts/release_recovery.py scripts/release_upgrade_workers.py /app/scripts/
COPY --chown=frame:frame release_migration/ /app/release_migration/
COPY --chown=frame:frame release_workers/ /app/release_workers/
COPY --chown=frame:frame deploy/release/postgres-rls.sql deploy/release/postgres-roles.sql /app/deploy/release/
COPY --chown=frame:frame deploy/release/postgres-dispatch-role.sql /app/deploy/release/
COPY --chown=frame:frame deploy/release/migrations/002_worker_dispatch.sql /app/deploy/release/migrations/
COPY --chown=frame:frame release_backend/ /app/release_backend/
COPY --chown=frame:frame release_billing/ /app/release_billing/
COPY --chown=frame:frame release_delivery/ /app/release_delivery/
COPY --chown=frame:frame release_web/ /app/release_web/
COPY --chown=frame:frame templates/ /app/templates/
COPY --chown=frame:frame static/ /app/static/
USER 10001:10001
ENV PYTHONUNBUFFERED=1 DATA_ROOT=/data MEDIA_ROOT=/data/media
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request,urllib.parse; request=urllib.request.Request('http://127.0.0.1:8000/api/v1/server',headers={'Host':urllib.parse.urlsplit(os.environ['PUBLIC_ORIGIN']).netloc}); urllib.request.urlopen(request,timeout=3)"
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "/dev/null", "release_app:create_app()"]
