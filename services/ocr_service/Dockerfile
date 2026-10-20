FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_THREAD_LIMIT=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por tesseract-ocr-eng fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY packages packages
COPY services services
RUN useradd --uid 10001 --create-home app
USER app
EXPOSE 8000
CMD ["uvicorn", "services.ocr_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
