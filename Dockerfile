FROM python:3.11-slim

WORKDIR /app

# Install git (needed for cloning repos)
RUN apt-get update && apt-get install -y git openssh-client && rm -rf /var/lib/apt/lists/*

# Configure git: use credentials file, and create a writable copy location
# The mounted .git-credentials is read-only, so we copy it at startup
RUN git config --global credential.helper 'store --file /tmp/.git-credentials'

# Copy and install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY src/ src/
COPY config.yaml .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Persistent storage for vector DB and cloned repos
VOLUME ["/data"]

EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
