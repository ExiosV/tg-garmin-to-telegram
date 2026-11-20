FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd -r -u 10001 app \
    && mkdir -p /data && chown app:app /data
USER app \
    && useradd -r -u 10001 app \
    && mkdir -p /data && chown app:app /data
USER app
ENTRYPOINT ["python", "-m", "tg_garmin_to_telegram"]
CMD ["run"]
