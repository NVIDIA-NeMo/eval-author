FROM debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2
RUN apt-get update && apt-get install -y --no-install-recommends squid && rm -rf /var/lib/apt/lists/*
COPY squid.conf /etc/squid/squid.conf
# runc hands the container's stdio to the container user; squid's own root-to-proxy drop cannot reopen it.
USER proxy
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
