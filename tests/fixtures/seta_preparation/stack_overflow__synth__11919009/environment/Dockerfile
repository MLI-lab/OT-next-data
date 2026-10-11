FROM ubuntu@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends bash ca-certificates coreutils curl gzip tar tmux python3 openssh-client dbus && rm -rf /var/lib/apt/lists/*
WORKDIR /app
