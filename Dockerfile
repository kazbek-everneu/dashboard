FROM python:3.12-slim

WORKDIR /app

# System dependencies for lxml, asyncpg, and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libpq-dev \
    libxml2-dev \
    libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies. The playwright pip package ships the client used
# to drive a remote browser via BROWSER_WS_ENDPOINT; the heavy browser binaries
# are NOT installed here — they run in the separate `browser` container built
# from the sibling repo everneu-screenshots-browsers (see compose.dev.yml). For
# local non-Docker dev with BROWSER_WS_ENDPOINT empty, run
# `python -m playwright install` manually.
COPY pyproject.toml ./
# --timeout/--retries make the build resilient to slow links: the playwright
# wheel is ~46 MB and the default 15s read timeout aborts mid-download.
RUN pip install --no-cache-dir --timeout 300 --retries 10 .

COPY . .

# Default command runs the API; override in docker-compose for worker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
