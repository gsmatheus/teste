FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 STT_CACHE=/data/models HF_HOME=/data/hf
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt && useradd -m -u 10001 stt && mkdir -p /data && chown stt /data

COPY app ./app
USER stt
EXPOSE 8000
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=4)"

# um processo só: o modelo fica carregado e a fila de 1 vale para o servidor inteiro
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
