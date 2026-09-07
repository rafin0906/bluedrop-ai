FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd -m bridge && mkdir /data && chown bridge:bridge /data
COPY broker.py .
USER bridge
EXPOSE 8000
CMD ["uvicorn", "broker:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
