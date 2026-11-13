FROM python:3.12-slim
WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY prazos ./prazos
ENV PYTHONUNBUFFERED=1 TZ=America/Sao_Paulo
CMD ["python", "-m", "prazos.rodar"]
