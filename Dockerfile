FROM python:3.12-slim
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ app/
COPY static/ static/

# Create directory for generated apps
RUN mkdir -p /tmp/generated-apps

EXPOSE 8000
ENV PORT=8000

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
