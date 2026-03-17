FROM python:3.11-slim

WORKDIR /app

# Install git (needed for cloning repos)
RUN apt-get update && apt-get install -y git openssh-client && rm -rf /var/lib/apt/lists/*

# Configure git credentials
RUN git config --global credential.helper 'store --file /tmp/.git-credentials'

# Install Python dependencies (this layer is cached unless requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (changes here don't re-trigger pip install)
COPY src/ src/
COPY config.yaml .
COPY entrypoint.sh .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["./entrypoint.sh"]
