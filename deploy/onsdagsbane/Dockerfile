FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ONSDAGSBANE_DB=/data/onsdagsbane.sqlite3
COPY pyproject.toml README.md ./
COPY src ./src
COPY omvendt_respit ./omvendt_respit
COPY analysis/models/redress_group_q_cache_mle.json ./analysis/models/redress_group_q_cache_mle.json
RUN pip install --no-cache-dir -e '.[web]' && mkdir -p /data
EXPOSE 8090
CMD ["gunicorn", "onsdagsbane.app:create_app()", "--bind", "0.0.0.0:8090", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--access-logformat", "%(m)s %(U)s %(s)s"]
