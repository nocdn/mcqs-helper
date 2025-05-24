FROM ghcr.io/astral-sh/uv:alpine

WORKDIR /app

COPY requirements.txt .

RUN uv pip install --system -r requirements.txt

COPY . .

EXPOSE 7480

CMD ["uv", "run", "app.py"]