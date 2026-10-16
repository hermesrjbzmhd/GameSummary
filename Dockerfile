FROM python:3.10-slim

# Install FFmpeg and essential libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy repository code
COPY . .

# Set default port
ENV PORT=8000
EXPOSE 8000

# Start bot in 24/7 daemon mode
CMD ["python", "GameSummary.py"]
