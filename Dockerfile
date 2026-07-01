FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir -e ".[web]"

# Optional OSINT binaries available via pip
RUN pip install --no-cache-dir holehe sherlock-project sublist3r

RUN mkdir -p /app/reports

EXPOSE 8080

# --allow-remote is required for a non-loopback bind (GHSA-cqr4-hcfp-m6m4) —
# safe here because the container network boundary is what's actually
# exposed; publish the port only to trusted networks.
CMD ["openosint", "web", "--host", "0.0.0.0", "--port", "8080", "--no-browser", "--allow-remote"]
