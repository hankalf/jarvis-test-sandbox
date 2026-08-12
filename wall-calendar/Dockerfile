FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

# /config is where a mounted config.yaml lands (docker-compose does this).
# Hosting platforms have no such file; the app falls back to defaults plus
# WALLDISPLAY_* variables, so a missing file here is not an error.
ENV WALLDISPLAY_CONFIG=/config/config.yaml \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# Not `uvicorn --port 8080`: hosting platforms assign a port through $PORT, and
# hardcoding one means the healthcheck never passes. Going through the module's
# own entrypoint keeps that decision in config.py where the rest of it lives.
CMD ["python", "-m", "app.main"]
