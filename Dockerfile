# Account Hub в контейнере: бот + Chromium для карточек. Всё изменяемое — в томе /data.
#   docker compose up -d        (см. docker-compose.yml)
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1     PYTHONDONTWRITEBYTECODE=1     PIP_NO_CACHE_DIR=1     PIP_DISABLE_PIP_VERSION_CHECK=1     PLAYWRIGHT_BROWSERS_PATH=/ms-playwright     HUB_ENV_FILE=/data/.env     DB_PATH=/data/hub.db     HUB_LOG_FILE=/data/hub.log

WORKDIR /app

# зависимости отдельным слоем — пересобираются, только если поменялся requirements.txt
COPY hub/requirements.txt .
RUN pip install -r requirements.txt  && python -m playwright install --with-deps --only-shell chromium  && rm -rf /var/lib/apt/lists/*

COPY hub/ .

# не root: uid 1000 — обычно совпадает с владельцем ./data на хосте
RUN useradd --uid 1000 --create-home hub && mkdir -p /data && chown hub:hub /data
USER hub
VOLUME /data

CMD ["python", "main.py"]
