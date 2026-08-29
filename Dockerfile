# StakeRoute application image. One image, three entrypoints selected by
# docker-compose's `command:` — worker, dashboard, simulator (D-008).
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src ./src

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"
ENV STAKEROUTE_TRANSPORT=jetstream
ENV STAKEROUTE_NATS_URL=nats://nats:4222
ENV STAKEROUTE_DB_PATH=/data/stakeroute.db
# WAL mode's shared-memory locking is unreliable across separate
# containers on this host's Docker Desktop volume backend — see the note
# in storage/repository.py. DELETE (SQLite's classic rollback journal) is
# the portable fallback for the multi-process deployment.
ENV STAKEROUTE_SQLITE_JOURNAL_MODE=DELETE

VOLUME ["/data"]

EXPOSE 8000

CMD ["uvicorn", "stakeroute.dashboard.main:app", "--host", "0.0.0.0", "--port", "8000"]
