FROM python:3.11-slim

WORKDIR /app

RUN pip install uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY tradebot.py .
COPY strategies/vanilla/tradebot.py strategies/vanilla/tradebot.py

RUN mkdir /data
ENV DATA_DIR=/data
VOLUME ["/data"]

CMD ["python", "-u", "tradebot.py"]
