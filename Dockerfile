FROM ghcr.io/astral-sh/uv:python3.12-alpine

WORKDIR /app

COPY requirements.txt .

RUN uv pip install --system -r requirements.txt

COPY . .

EXPOSE 7480

CMD ["uv", "run", "gunicorn", "--bind", "0.0.0.0:7480", "app:app"]