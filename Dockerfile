FROM python:3.12-slim AS runtime

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCALPER_AI_CONFIG_DIR=/app/configs \
    SCALPER_AI_DATA_RAW_DIR=/app/data/raw \
    SCALPER_AI_DATA_PROCESSED_DIR=/app/data/processed \
    SCALPER_AI_DATA_ARTIFACTS_DIR=/app/data/artifacts

WORKDIR /app

RUN groupadd --system scalper_ai \
    && useradd --system --gid scalper_ai --home-dir /app --shell /usr/sbin/nologin scalper_ai

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY configs ./configs
COPY scripts ./scripts

RUN mkdir -p /app/data/raw /app/data/processed /app/data/artifacts \
    && chown -R scalper_ai:scalper_ai /app

USER scalper_ai

ENTRYPOINT ["python", "scripts/run_runtime.py"]
CMD ["describe", "--config-name", "paper"]
