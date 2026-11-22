# Baresip-Speakerphone-MCP: baresip (built from source) + the Python controller/MCP server.
ARG PY_IMAGE=python:3.13-slim-trixie
ARG RE_VERSION=v4.13.0
ARG BARESIP_VERSION=v4.13.0

FROM ${PY_IMAGE} AS build
ARG RE_VERSION
ARG BARESIP_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates pkg-config \
      libssl-dev zlib1g-dev libopus-dev libpulse-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git clone --depth 1 -b ${RE_VERSION} https://github.com/baresip/re.git \
 && git clone --depth 1 -b ${BARESIP_VERSION} https://github.com/baresip/baresip.git
RUN cmake -S re -B re/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/baresip \
 && cmake --build re/build -j"$(nproc)" && cmake --install re/build
RUN cmake -S baresip -B baresip/build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/baresip \
      -DCMAKE_PREFIX_PATH=/opt/baresip -Dre_DIR=/opt/baresip/lib/cmake/re \
      -DMODULES="account;auconv;aufile;auresamp;ctrl_tcp;g711;menu;opus;pulse;stdio" \
 && cmake --build baresip/build -j"$(nproc)" && cmake --install baresip/build

FROM ${PY_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends \
      libssl3t64 zlib1g libopus0 libpulse0 pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/baresip /opt/baresip
ENV PATH=/opt/baresip/bin:$PATH LD_LIBRARY_PATH=/opt/baresip/lib
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
COPY src ./src
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev
ENV PYTHONUNBUFFERED=1
CMD ["/app/.venv/bin/speakerphone"]
