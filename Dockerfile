FROM python:3.11-slim

WORKDIR /app

RUN pip install uv

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY tradebot.py .

CMD ["python", "-u", "tradebot.py"]
