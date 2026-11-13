FROM python:3.12-slim
WORKDIR /app
COPY requirements-cloud.txt .
RUN pip install --no-cache-dir -r requirements-cloud.txt
COPY prazos ./prazos
COPY app ./app
ENV PYTHONUNBUFFERED=1 TZ=America/Sao_Paulo
# Mesmo contêiner para os dois papéis: no serviço (K_SERVICE definido) sobe a API/aplicativo;
# no job agendado roda a consulta ao DJEN.
CMD ["sh", "-c", "if [ -n \"$K_SERVICE\" ]; then exec gunicorn -b :${PORT:-8080} -w 1 --threads 8 --timeout 60 prazos.app:app; else exec python -m prazos.rodar; fi"]
