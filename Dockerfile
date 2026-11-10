FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . \
    && useradd --system --no-create-home --uid 10001 dqcheck \
    && install -d -o dqcheck /data

# The data and the rules file are read from /data (a volume or a bind mount) and the reports are written there.
# Nothing needs the network or a privilege: run with --network none, --read-only, --cap-drop ALL and --tmpfs /tmp
# (matplotlib's cache).
USER dqcheck
WORKDIR /data
VOLUME ["/data"]
ENTRYPOINT ["dqcheck"]
CMD ["--help"]
