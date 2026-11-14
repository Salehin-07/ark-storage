FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY arkstorage ./arkstorage
COPY wsgi.py gunicorn.conf.py ./
ENV ARK_DATA_DIR=/data PYTHONUNBUFFERED=1 PORT=8000
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz')"
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
