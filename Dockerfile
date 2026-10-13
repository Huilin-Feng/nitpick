FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY nitpick ./nitpick

# Do not run the server as root inside the container.
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser

# Mount a volume at /data so review history survives container restarts.
ENV NITPICK_DB_PATH=/data/nitpick.db

EXPOSE 8000
CMD ["uvicorn", "nitpick.main:app", "--host", "0.0.0.0", "--port", "8000"]
