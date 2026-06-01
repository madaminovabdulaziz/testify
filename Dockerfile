# Railway production image for the attestation bot.
#
# This is the image Railway builds (railway.toml → builder = DOCKERFILE). The
# compose / self-host path uses docker/Dockerfile instead; this one is pip-based
# and self-contained (no uv.lock dependency) so it builds cleanly on Railway.
#
# Multi-stage: the builder has a C toolchain to compile asyncmy (a Cython
# extension with no prebuilt wheel) into a wheel; the runtime stage installs
# from the prebuilt wheels and carries no toolchain, keeping it slim.

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
# build-essential: gcc/g++ to compile asyncmy. The python:slim image already
# ships the CPython headers needed for the extension build.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY app ./app
# Build wheels for the project + every dependency (compiling asyncmy) into /wheels.
RUN pip install --upgrade pip && pip wheel --wheel-dir=/wheels .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# Install the project + all deps from the prebuilt wheels (offline, no toolchain).
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels attestation-bot && rm -rf /wheels
# Migrations, helper scripts, and the entrypoint. The local app/ tree (with
# app/static/template.xlsx) shadows the installed package at runtime, so the
# bundled template and Alembic env are always present.
COPY app ./app
COPY alembic ./alembic
COPY scripts ./scripts
COPY alembic.ini start.sh ./
RUN chmod +x start.sh

# Railway provides $PORT and routes to it; 8080 is the local default.
EXPOSE 8080
CMD ["bash", "./start.sh"]
