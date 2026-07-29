# Production Dockerfile for DataPilot backend on Google Cloud Run.
FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for scipy/sklearn
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install data science packages for the Python tool subprocess fallback
RUN pip install --no-cache-dir scipy==1.14.1 scikit-learn==1.6.0

# Copy backend code
COPY backend/ .

# Create uploads directory
RUN mkdir -p /app/uploads

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
