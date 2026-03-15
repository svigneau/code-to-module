FROM mcr.microsoft.com/devcontainers/python:3.11

# nf-core tools
RUN pip install --no-cache-dir "nf-core>=2.14"

# nf-test
RUN curl -fsSL https://get.nf-test.com | bash && mv nf-test /usr/local/bin/

# Java runtime required by nf-test
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Nextflow (needed for integration tests)
RUN curl -s https://get.nextflow.io | bash && mv nextflow /usr/local/bin/
