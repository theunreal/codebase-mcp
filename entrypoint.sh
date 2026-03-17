#!/bin/bash
# Copy read-only mounted credentials to a writable location
if [ -f /root/.git-credentials ]; then
    cp /root/.git-credentials /tmp/.git-credentials
fi

exec python -m src.server "$@"
