FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy from root build context
COPY vscode_integration.py .

EXPOSE 8082
CMD ["uvicorn", "vscode_integration:app", "--host", "0.0.0.0", "--port", "8082", "--workers", "2", "--log-level", "info"]
