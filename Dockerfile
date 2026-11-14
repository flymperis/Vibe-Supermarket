FROM python:3.12-slim

WORKDIR /app
ENV DATABASE_PATH=/app/data/supermarket.db \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5050

# The catalog ships in static/catalog/ and is loaded into SQLite at startup; nothing is
# fetched from the internet. One worker with threads is plenty for two phones polling.
CMD ["gunicorn", "-w", "1", "--worker-class", "gthread", "--threads", "8", "-b", "0.0.0.0:5050", "--timeout", "60", "app:app"]
