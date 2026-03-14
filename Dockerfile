FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy source code
COPY . .

# Install the package
RUN pip install --no-cache-dir -e .

ENTRYPOINT ["proxmox-config"]
CMD ["--help"]
