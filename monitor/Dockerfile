FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Train the model at build time so every container starts from the same frozen
# model + reference dataset (the "known good" baseline drift is measured against).
RUN python -m app.train \
 && useradd --create-home --uid 1000 monitor \
 && mkdir -p /app/reports \
 && chown -R monitor:monitor /app

USER monitor
EXPOSE 8000

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
