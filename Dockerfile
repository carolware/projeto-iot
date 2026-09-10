FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sensor.py gateway.py atuador.py ./

CMD ["python", "sensor.py"]
