FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY docker/entrypoint.sh /usr/local/bin/finpath-entrypoint.sh

RUN pip install --no-cache-dir . \
    && chmod +x /usr/local/bin/finpath-entrypoint.sh \
    && useradd --create-home --uid 10001 finpath \
    && mkdir -p /app/var/email_outbox \
    && chown -R finpath:finpath /app/var/email_outbox \
    && chmod 0700 /app/var/email_outbox

USER finpath

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/finpath-entrypoint.sh"]
