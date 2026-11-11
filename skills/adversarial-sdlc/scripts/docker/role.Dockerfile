# sdlc-role:local - sdlc-gate plus Node and pi. Extensions are NOT baked in: they are mounted read-only.
ARG BASE=sdlc-gate:local
FROM ${BASE}

LABEL org.opencontainers.image.title=sdlc-role

ARG NODE_VERSION=26.12.1
ARG NODE_SHA256_ARM64=42aa04922591ee5d4dbc7740d452621acf28508239daa512f5ee8c47ee224fdb
ARG NODE_SHA256_X64=6170b2d474e1c626f43cff3adc118df9fe3b573258a4fc3d850c8dbc6b079bbd
ARG PI_VERSION=1.4.0

USER root
RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
      arm64) node_arch=arm64; node_sha="${NODE_SHA256_ARM64}" ;; \
      amd64) node_arch=x64;   node_sha="${NODE_SHA256_X64}" ;; \
      *) echo "unsupported architecture" >&2; exit 1 ;; \
    esac; \
    f="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"; \
    apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends xz-utils libatomic1 libstdc++6; \
    curl -fsSL -o "/tmp/${f}" "https://nodejs.org/dist/v${NODE_VERSION}/${f}"; \
    echo "${node_sha}  /tmp/${f}" | sha256sum -c -; \
    tar -xJf "/tmp/${f}" -C /usr/local --strip-components=1 --no-same-owner \
      --exclude='*/CHANGELOG.md' --exclude='*/LICENSE' --exclude='*/README.md'; \
    rm "/tmp/${f}"; \
    apt-get purge -y xz-utils; rm -rf /var/lib/apt/lists/*; \
    node --version | grep -qx "v${NODE_VERSION}"; \
    npm install -g "@earendil-works/pi-coding-agent@${PI_VERSION}"; \
    npm cache clean --force; rm -rf /root/.npm /tmp/*; \
    HOME=/tmp pi --version 2>&1 | grep -qx "${PI_VERSION}"

USER 10001
