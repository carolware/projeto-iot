FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# nucleo/ é importado pelos serviços; contratos/ é lido pela validação.
# PYTHONUNBUFFERED: sem ele os print() do atuador/sensor ficam presos no
# buffer e não aparecem em `docker compose logs`.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1
COPY nucleo ./nucleo
COPY contratos ./contratos
COPY central ./central
COPY borda ./borda
COPY simuladores ./simuladores

CMD ["python", "simuladores/estacao/sensor.py"]
