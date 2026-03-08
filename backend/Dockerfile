FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Environment variable for persistence
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
