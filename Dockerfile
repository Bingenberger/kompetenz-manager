FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer poppler-utils fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 kompetenz && useradd --uid 10001 --gid 10001 --no-create-home kompetenz
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY *.py ./
COPY routes ./routes
COPY templates ./templates
COPY static ./static
COPY gunicorn.conf.py ./
RUN mkdir -p /app/instance /app/static/uploads /app/odt_templates \
 && chown -R 10001:10001 /app/instance /app/odt_templates
FROM base AS tests
COPY requirements-test.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-test.lock
COPY tests ./tests
COPY contract_tests ./contract_tests
COPY odt_templates ./odt_templates
USER 10001:10001
FROM base AS runtime
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=12 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=2)"
CMD ["gunicorn","--config","gunicorn.conf.py","app:app"]
