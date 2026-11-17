#!/usr/bin/env bash
# Runs the Kotlin templates' tests (engine self-test + skeleton simulation; the @Tag("docker") conformance test is
# compiled but excluded). Needs JDK 21 (the pinned Gradle 8.10 and Kotlin 2.0 cannot run on newer JDKs such as 25).
# CI: set JAVA_HOME via actions/setup-java 21. On macOS with another default JDK this picks an installed 21.
# Extra args go to Gradle.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
if [ -x /usr/libexec/java_home ]; then
  jh="$(/usr/libexec/java_home -v 21 2>/dev/null || true)"
  [ -n "$jh" ] && export JAVA_HOME="$jh"
fi
cd "$here/kotlin"
exec ./gradlew --no-daemon --console=plain test "$@"
