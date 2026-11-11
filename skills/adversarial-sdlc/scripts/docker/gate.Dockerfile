# sdlc-gate:local - JDK 21, Gradle 8.10 and Python 3 for gate steps and code under test. No agent, no Node.
# Roles and gate steps run as an arbitrary --user HOSTUID:HOSTGID with a read-only root fs, so nothing
# here may depend on the user existing or on a writable path outside the tmpfs/bind mounts.
FROM debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4

LABEL org.opencontainers.image.title=sdlc-gate

ARG GRADLE_VERSION=8.10
ARG GRADLE_SHA256=5b9c5eb3f9fc2c94abaea57d90bd78747ca117ddbbf96c859d3741181a12bf2a

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      openjdk-21-jdk-headless python3 bash coreutils procps findutils curl ca-certificates unzip \
 && rm -rf /var/lib/apt/lists/* \
 # Arch-independent stable JAVA_HOME (the Debian dir is java-21-openjdk-<arch>).
 && ln -s "$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")" /usr/lib/jvm/sdlc-java-21 \
 && /usr/lib/jvm/sdlc-java-21/bin/java -version

ENV JAVA_HOME=/usr/lib/jvm/sdlc-java-21
ENV PATH=/usr/lib/jvm/sdlc-java-21/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Gradle distribution (used by warmup.sh to generate wrappers), checksum verified.
RUN curl -fsSL -o /tmp/gradle.zip "https://services.gradle.org/distributions/gradle-${GRADLE_VERSION}-bin.zip" \
 && echo "${GRADLE_SHA256}  /tmp/gradle.zip" | sha256sum -c - \
 && unzip -q /tmp/gradle.zip -d /opt \
 && rm /tmp/gradle.zip \
 && test -x /opt/gradle-${GRADLE_VERSION}/bin/gradle

# The distribution exactly as the Gradle wrapper lays it out (wrapper/dists/gradle-X-bin/<hash>/gradle-X
# plus the .ok marker). The wrapper writes <dist>.lck next to it, so containers copy this tree into their
# tmpfs GRADLE_USER_HOME/wrapper/dists at start (spec A-2) instead of using it in place.
RUN set -eux; \
    mkdir /tmp/wrapperproj; cd /tmp/wrapperproj; touch settings.gradle; \
    /opt/gradle-${GRADLE_VERSION}/bin/gradle --no-daemon wrapper --gradle-version ${GRADLE_VERSION} --distribution-type bin; \
    GRADLE_USER_HOME=/opt/gradle-seed ./gradlew --no-daemon --version; \
    mv /opt/gradle-seed/wrapper/dists /opt/gradle-dists; \
    find /opt/gradle-dists -name '*.lck' -delete; \
    chmod -R a+rX /opt/gradle-dists; \
    ls /opt/gradle-dists/gradle-${GRADLE_VERSION}-bin/*/; \
    rm -rf /opt/gradle-seed /tmp/wrapperproj /root/.gradle /tmp/hsperfdata_root

# /home/sdlc and /work are tmpfs mountpoints at run time; /opt/gradle-ro is the read-only cache volume.
RUN mkdir -p /home/sdlc /opt/gradle-ro /work \
 && chmod 1777 /home/sdlc /work \
 && printf 'sdlc-docker-backend\n' > /etc/sdlc-container \
 && chown root:root /etc/sdlc-container && chmod 0444 /etc/sdlc-container \
 && useradd --uid 10001 --user-group --home-dir /home/sdlc --no-create-home --shell /bin/bash sdlc

ENV HOME=/home/sdlc
WORKDIR /work
USER 10001
CMD ["bash"]
