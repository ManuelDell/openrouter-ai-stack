FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

# ── Base system packages ──────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl wget jq git \
      python3 python3-pip \
      openssh-client \
      netcat-openbsd dnsutils iputils-ping \
      nano vim less \
      unzip zip \
      ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 18 (LTS) via NodeSource — replaces system Node 12 ─
RUN curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── Puppeteer system dependencies (Chromium libs) ────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
      libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
      libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
      libgbm1 libasound2 libpangocairo-1.0-0 libpango-1.0-0 \
      libx11-xcb1 libxcb-dri3-0 libxshmfence1 \
      fonts-liberation libappindicator3-1 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# ── Puppeteer (includes bundled Chromium) ────────────────────
# Store Chromium in a fixed cache path inside the image
ENV PUPPETEER_CACHE_DIR=/usr/local/share/puppeteer
RUN npm install -g puppeteer@21 --unsafe-perm 2>&1 | tail -5 \
    && npm root -g

# NODE_PATH: allows `require('puppeteer')` in LLM scripts without installing locally
# Set after install so it doesn't invalidate the npm cache layer on changes
ENV NODE_PATH=/usr/lib/node_modules

# Docker requires --no-sandbox. Pre-set as env var so LLM scripts can reference it.
ENV PUPPETEER_ARGS="--no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage"

# ── Python HTTP service ───────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY app.py .

RUN mkdir -p /workspace

EXPOSE 8090

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
