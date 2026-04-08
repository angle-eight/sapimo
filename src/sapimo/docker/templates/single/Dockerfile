ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

LABEL maintainer="sapimo" \
    description="Sapimo single-container runtime"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY api_mock/docker/single/requirements.txt /workspace/
RUN pip install --no-cache-dir -r requirements.txt

COPY api_mock/docker/gateway/main.py /workspace/main.py
COPY api_mock/docker/gateway/mock_handler.py /workspace/mock_handler.py
COPY api_mock/docker/gateway/openapi_example_resolver.py /workspace/openapi_example_resolver.py
COPY api_mock/docker/sapimo/ /workspace/sapimo/
COPY api_mock/ /workspace/api_mock/

RUN mkdir -p /workspace/logs /workspace/api_mock/log && \
    chmod -R 755 /workspace/logs /workspace/api_mock/log

ENV SAPIMO_MODE=single-container \
    SAPIMO_SINGLE_CONTAINER=1 \
    PYTHONPATH=/workspace:/workspace/api_mock/docker \
    SAPIMO_HOST=0.0.0.0 \
    SAPIMO_PORT=3000 \
    LOG_DIR=/workspace/logs

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1

EXPOSE 3000

CMD ["python", "/workspace/api_mock/docker/gateway/main.py"]
