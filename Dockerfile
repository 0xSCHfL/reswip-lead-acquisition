FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY app ./app
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY profiles ./profiles

RUN pip install --no-cache-dir -e ".[dev]"

ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
