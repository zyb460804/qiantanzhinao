FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/home/app/.local/bin:$PATH
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /home/app --create-home app
COPY --from=builder /install /usr/local
COPY --chown=app:app alembic.ini ./alembic.ini
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app app/ ./app/
RUN mkdir -p /app/uploads && chown app:app /app/uploads
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
