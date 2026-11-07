FROM python:3.11-slim

# Prevent Python from writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1

# Show logs immediately
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# No compiler or libpq needed: asyncpg ships manylinux wheels and psycopg2 is
# not used. pip runs as root, the app does not.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Unprivileged runtime user
RUN groupadd --system app && useradd --system --gid app --no-create-home app

# Copy the application (secrets are excluded by .dockerignore)
COPY --chown=app:app . .

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
