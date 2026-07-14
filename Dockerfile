FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GAMEFUNDS_DATA_DIR=/app/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 gamefunds

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY data/rubrics.json ./data/rubrics.json
COPY data/help ./data/help

RUN pip install --upgrade pip \
    && pip install -e ".[http]"

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown -R gamefunds:gamefunds /app

EXPOSE 8080

# Starts as root only to fix data-volume ownership, then drops to `gamefunds`.
ENTRYPOINT ["/entrypoint.sh"]
