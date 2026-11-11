# sdlc-egress:local - the allowlisting forward/reverse proxy (egress.py, stdlib only).
FROM debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4

LABEL org.opencontainers.image.title=sdlc-egress

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*

COPY egress.py /opt/sdlc/egress.py

USER 10001
EXPOSE 8080
CMD ["python3", "/opt/sdlc/egress.py"]
