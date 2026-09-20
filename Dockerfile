# The full featured target: everything the serverless deployment cannot do.
# Playwright rendering, the PyTorch sentence encoder, unbounded crawls and an
# in process scheduler all switch on here because app/runtime.py detects a
# server tier and stops reporting them as unavailable.
#
# Build:  docker build -t newsroom .
# Run:    docker run -p 8000:8000 --env-file .env -v newsroom-data:/data newsroom

FROM node:22-slim AS web

WORKDIR /build
COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY next.config.mjs tsconfig.json postcss.config.mjs tailwind.config.ts ./
COPY app ./app
COPY components ./components
COPY lib ./lib
COPY public ./public
# The browser bundle is baked at build time, so it talks to the same origin
# the Python process serves it from.
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    MODELS_DIR="/models" \
    NODE_ENV=production

# curl is for the health check; the rest are Chromium's shared library
# dependencies, installed here so `playwright install chromium` has something
# to link against.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates nodejs npm \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libasound2 libpango-1.0-0 libcairo2 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt requirements-server.txt ./

# The CPU wheel is installed explicitly. The default PyPI torch pulls several
# hundred megabytes of CUDA libraries that are useless on a CPU only host.
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements-server.txt

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install chromium

COPY server ./server
COPY scripts ./scripts
COPY --from=web /build/.next ./.next
COPY --from=web /build/node_modules ./node_modules
COPY --from=web /build/public ./public
COPY package.json next.config.mjs ./

# Bake the local sentence encoder into the image so a container starts ready
# rather than downloading 23 MB on its first request.
RUN mkdir -p /models /data \
    && python scripts/fetch_model.py --dir /models/all-MiniLM-L6-v2-onnx --quiet \
    && python -c "from sentence_transformers import SentenceTransformer as S; \
S('sentence-transformers/all-MiniLM-L6-v2', cache_folder='/models')"

ENV LOCAL_EMBED_MODEL_DIR=/models/all-MiniLM-L6-v2-onnx
VOLUME ["/data"]
EXPOSE 8000 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["all"]
