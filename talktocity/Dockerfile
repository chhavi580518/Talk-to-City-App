# talktocity/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# 1. Upgrade pip first (as its own cached layer)
# Using 'python -m pip' is the safest way to upgrade pip itself
RUN python -m pip install --no-cache-dir --upgrade pip

# 2. Copy requirements.txt and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=900 --retries=10 -r requirements.txt

# 3. Copy application code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]