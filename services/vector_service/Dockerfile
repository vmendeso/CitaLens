FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock requirements.txt ./
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home app
COPY packages ./packages
COPY services ./services
USER app
EXPOSE 8000
CMD ["uvicorn", "services.vector_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
