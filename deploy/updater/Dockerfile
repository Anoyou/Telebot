FROM python:3.12-alpine

RUN apk add --no-cache bash ca-certificates curl docker-cli docker-cli-compose git openssh-client

WORKDIR /workspace

COPY deploy/updater/server.py /app/server.py

ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD ["python", "/app/server.py"]
