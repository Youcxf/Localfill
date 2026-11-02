# --- frontend ---
# builds the angular dashboard in web/. "browser" is where angular puts the built files
FROM node:22-alpine AS web
WORKDIR /web
# the build copies logo.png and icon.png from the repository root, which is /web/.. here
COPY logo.png icon.png /
COPY web/package*.json ./
RUN npm ci
COPY web ./
RUN npm run build -- --configuration production


# --- backend ---
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOCALFILL_DATA_DIR=/data \
    LOCALFILL_STATIC_DIR=/app/static \
    LOCALFILL_INGRESS_HOST=0.0.0.0 \
    LOCALFILL_ADMIN_HOST=0.0.0.0

WORKDIR /app

# install the dependencies first so docker can cache this step
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/app ./app
COPY --from=web /web/dist/localfill/browser ./static

# do not run as root. /data is where the database and the encryption key live
RUN useradd --create-home --uid 1000 localfill \
    && mkdir /data \
    && chown localfill /data
USER localfill

VOLUME /data
# 8010 = webhook (the one you expose), 8011 = dashboard and api (keep it private)
EXPOSE 8010 8011

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8011/api/health', timeout=3)"

CMD ["python", "-m", "app.main"]
