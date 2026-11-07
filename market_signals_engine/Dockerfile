FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY market_signals_engine /app/market_signals_engine

CMD ["python", "-m", "market_signals_engine.scheduler"]
