FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app app
COPY prompts prompts
COPY corpus corpus
COPY static static
COPY eval eval
COPY scripts scripts
COPY loadtest loadtest
RUN mkdir -p data
# index is built by the one-shot 'ingest' service into a shared volume; services wait for it (see docker-compose.yml)
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
