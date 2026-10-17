# The TrustBench server and worker. One image, two commands:
#   trustbench serve --host 0.0.0.0     the API
#   trustbench worker                   background tasks
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[redis]"

# Do not run as root.
RUN useradd --create-home --uid 10001 trustbench
USER trustbench

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=3).status == 200 else 1)"
CMD ["trustbench", "serve", "--host", "0.0.0.0", "--port", "8000"]
