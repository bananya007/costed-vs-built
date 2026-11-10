FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY generator ./generator
COPY pipeline ./pipeline
COPY sql ./sql
COPY app ./app

# Serves the app; the seed service overrides the command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
