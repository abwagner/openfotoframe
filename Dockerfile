# OpenFotoFrame - Docker Image
# Supports both AMD64 and ARM64 (Raspberry Pi 4)

FROM python:3.14-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py
ENV FLASK_ENV=production

# Install rclone (multi-arch: AMD64 + ARM64) and curl (needed for healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl unzip v4l-utils && \
    curl -fsSL https://rclone.org/install.sh | bash && \
    apt-get purge -y unzip && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash photoframe

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .
COPY render_display.py .
COPY artwork_catalog.py .
COPY templates templates/
COPY static static/

# Create directories for persistent data
RUN mkdir -p /app/uploads /app/uploads/thumbnails /app/data /app/data/rclone && \
    chown -R photoframe:photoframe /app

# Switch to non-root user
USER photoframe

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/login || exit 1

# Run with gunicorn for production
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "app:app"]
