FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for config disk creation
RUN apt-get update && apt-get install -y --no-install-recommends \
    mtools \
    && rm -rf /var/lib/apt/lists/*

# Copy all source files first
COPY . .

# Install the package
RUN pip install --no-cache-dir -e .

ENTRYPOINT ["proxmox-config"]
CMD ["--help"]
