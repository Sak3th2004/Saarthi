# MCP server container for App Runner / Lightsail. Build from repo root:
#   docker build -f mcp-server/Dockerfile -t saarthi-mcp ./mcp-server
# Do not bake secrets into the image. Pass Cognito + Neo4j via runtime env / Secrets Manager.
FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV SAARTHI_HOST=0.0.0.0
ENV SAARTHI_PORT=8080
ENV SAARTHI_AUTH=cognito
ENV SAARTHI_AGENTS=off
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "-m", "saarthi_mcp"]
