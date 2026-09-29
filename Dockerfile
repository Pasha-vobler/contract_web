FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/output

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app:app --host ${APP_HOST:-0.0.0.0} --port ${APP_PORT:-8000} --workers 1"]
