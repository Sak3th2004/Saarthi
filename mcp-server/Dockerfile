# Build from repo root: docker build -f mcp-server/Dockerfile -t saarthi-mcp .
# Do not bake secrets into the image. Pass Cognito + Neo4j via runtime env / Secrets Manager.
FROM node:22-slim AS dashboard
WORKDIR /build/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

COPY constraints.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 120 --retries 8 -c constraints.txt mcp
COPY agents/ ./agents/
COPY mcp-server/ ./mcp-server/
COPY --from=dashboard /build/dashboard/dist ./dashboard/

RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 120 --retries 8 -c constraints.txt ./agents './mcp-server[agents,calendar]' && \
    useradd --create-home --uid 10001 saarthi

ENV SAARTHI_HOST=0.0.0.0
ENV SAARTHI_PORT=8080
ENV SAARTHI_AUTH=cognito
ENV SAARTHI_AGENTS=off
ENV PYTHONUNBUFFERED=1
ENV SAARTHI_DASHBOARD_DIR=/app/dashboard
USER saarthi

EXPOSE 8080

CMD ["python", "-m", "saarthi_mcp"]
