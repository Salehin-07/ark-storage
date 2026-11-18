FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
LABEL org.opencontainers.image.source="https://github.com/Salehin-07/ark-storage" \
      org.opencontainers.image.description="Ark Storage: your own cloud drive, stored in a GitHub repository" \
      org.opencontainers.image.licenses="MIT"
# Optional: docker build --build-arg WITH_OFFICE=1 . adds LibreOffice so old .doc/.ppt/.odt/.rtf files can be previewed (~500 MB larger)
ARG WITH_OFFICE=0
RUN if [ "$WITH_OFFICE" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends libreoffice-writer libreoffice-impress fonts-liberation fonts-noto-core && rm -rf /var/lib/apt/lists/*; fi
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY arkstorage ./arkstorage
ENV ARK_DATA_DIR=/data PYTHONUNBUFFERED=1 PORT=8000
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz')"
CMD ["python", "-m", "arkstorage"]
