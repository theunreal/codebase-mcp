FROM python:3.11-slim

WORKDIR /app

# Install git (needed for cloning repos)
RUN apt-get update && apt-get install -y git openssh-client && rm -rf /var/lib/apt/lists/*

# Configure git to use credentials file if mounted
RUN git config --global credential.helper store

# Copy and install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY src/ src/
COPY config.yaml .

# Persistent storage for vector DB and cloned repos
VOLUME ["/data"]

EXPOSE 8080

CMD ["python", "-m", "src.server"]
