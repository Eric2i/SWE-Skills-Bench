FROM node:22-bookworm

# Generic local benchmark image for Python-based SWE-Skills-Bench tasks.
# Goals:
# - works on Apple Silicon (official multi-arch base image)
# - includes Python tooling, git, and common build utilities
# - includes Codex CLI for the new codex backend
# - provides the dev user and filesystem layout expected by the benchmark runner

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV HOME=/home/dev
ENV WORKSPACE=/workspace
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:/home/dev/.local/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    ca-certificates \
    curl \
    git \
    jq \
    less \
    openssh-client \
    procps \
    python3 \
    python3-dev \
    python3-pip \
    python3-venv \
    python-is-python3 \
    ripgrep \
    sudo \
    tini \
    unzip \
    vim \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install --no-cache-dir \
    ipython \
    pydantic \
    pydantic-settings \
    pytest \
    pytest-xdist \
    requests \
    setuptools \
    wheel

RUN npm install -g @openai/codex

RUN useradd -m -s /bin/bash dev \
 && echo "dev ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/dev \
 && chmod 0440 /etc/sudoers.d/dev \
 && mkdir -p /workspace /tmp/golden_reference /home/dev/.codex /home/dev/.agents/skills \
 && chown -R dev:dev /workspace /tmp/golden_reference /home/dev

WORKDIR /workspace
USER dev

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash"]
