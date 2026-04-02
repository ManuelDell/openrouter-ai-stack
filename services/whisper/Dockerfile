FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

# Model cache — downloaded on first start, persisted via volume
ENV WHISPER_MODEL=small
ENV WHISPER_MODEL_DIR=/data/models
ENV WHISPER_DEVICE=cpu
ENV WHISPER_COMPUTE_TYPE=int8

EXPOSE 8093
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8093", "--log-level", "info"]
