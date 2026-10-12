FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GROCERY_DB=/data/grocery.db

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Run as a non-root user; /data is where the SQLite database lives (mounted volume).
RUN useradd --create-home app && mkdir /data && chown app:app /data
USER app

EXPOSE 8000
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "app.main:app"]
