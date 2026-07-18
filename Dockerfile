FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "echo 'Starting gunicorn on port $PORT' && gunicorn flask_app:app --bind 0.0.0.0:${PORT:-8000} --timeout 120 --log-level debug"]
