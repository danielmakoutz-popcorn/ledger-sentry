FROM python:3.12-slim
WORKDIR /srv/ledger-sentry
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /data
ENV LEDGER_DATA_DIR=/data
EXPOSE 8787
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8787"]
