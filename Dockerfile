# ==================================================================
# STAGE 1 - BUILDER
# Baixa/compila as dependencias em um diretorio isolado (/install).
# Nenhum compilador ou cache de pip vai para a imagem final.
# ==================================================================
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .

# --prefix=/install isola tudo para copiar no stage final.
RUN pip install --upgrade pip \
    && pip install --prefix=/install -r requirements.txt


# ==================================================================
# STAGE 2 - RUNTIME
# Imagem final enxuta, sem toolchain de build.
# ==================================================================
FROM python:3.11-slim AS runtime

# libglib2.0-0    -> unica lib de sistema exigida pelo opencv headless
# fonts-dejavu-core -> fonte usada para imprimir o texto do cartao;
#                      sem ela o PIL cai numa fonte bitmap minuscula
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    # Evita que o OpenCV crie mais threads que os vCPUs da instancia.
    OPENCV_NUM_THREADS=1

COPY --from=builder /install /usr/local

WORKDIR /app

# Codigo da aplicacao e interface web
COPY main.py omr_engine.py gerador.py schemas.py \
     seguranca.py analitico.py escolaridade.py \
     estampa.py deteccao_grade.py lote.py comparativo.py \
     permissoes.py relatorio.py recuperar_admin.py ./
COPY dados/ ./dados/
COPY static/ ./static/

# Usuario sem privilegios (boa pratica de seguranca em container).
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8080\")}/api/health')" || exit 1

# 'exec' + shell form: expande ${PORT} e mantem o uvicorn como PID 1,
# necessario para o Cloud Run entregar SIGTERM no shutdown.
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 65
