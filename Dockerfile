# Two stages: build the virtualenv with pip, ship it without pip, setuptools or caches.
FROM python:3.13-alpine AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
WORKDIR /src
RUN python -m venv --without-pip /opt/venv
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip --python /opt/venv/bin/python install ".[service]"

FROM python:3.13-alpine
# The official image ships the standard library without .pyc files and the service runs
# as a non-root user, so without this every process start (each worker, the planner and
# every healthcheck) recompiles the stdlib in memory. Compiling once here halves start-up
# CPU. MALLOC_ARENA_MAX caps glibc's per-thread malloc arenas (the API serves sync
# endpoints from a thread pool), which keeps resident memory flat after bursts.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2 PATH=/opt/venv/bin:$PATH
RUN python -m compileall -q -j0 -x '/(site-packages|test|tests|idlelib|tkinter|turtledemo|ensurepip|lib2to3|pydoc_data)/' \
        /usr/local/lib/python3.13 \
 && adduser -S -u 10001 rubato
COPY --from=build /opt/venv /opt/venv
USER rubato
WORKDIR /app
EXPOSE 8000
CMD ["uvicorn", "rubato.service.app:app", "--host", "0.0.0.0", "--port", "8000"]
