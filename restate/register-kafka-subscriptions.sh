#!/bin/sh

# Idempotent Restate Kafka subscription registration.
# Checks the admin API before creating each subscription and treats HTTP 409 as success.
#
# Required:
#   RESTATE_ADMIN_URL   e.g. http://restate.restate.svc.cluster.local:9070
#   SERVICE_NAME        Restate service to deliver to
#   SERVICE_HANDLER     handler that accepts the Kafka event
#   TOPICS              space- or newline-separated topic names
# Optional:
#   KAFKA_CLUSTER       cluster name in the source URI (default: kafka)
#   MAX_RETRIES         admin readiness attempts (default: 30)
#   RETRY_DELAY_SECONDS (default: 2)
#
# The service must already be registered with Restate; this only wires Kafka to it.

set -eu

RESTATE_ADMIN="${RESTATE_ADMIN_URL:?set RESTATE_ADMIN_URL}"
SERVICE_NAME="${SERVICE_NAME:?set SERVICE_NAME}"
SERVICE_HANDLER="${SERVICE_HANDLER:?set SERVICE_HANDLER}"
TOPICS="${TOPICS:?set TOPICS}"
KAFKA_CLUSTER="${KAFKA_CLUSTER:-kafka}"
MAX_RETRIES="${MAX_RETRIES:-30}"
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-2}"

echo "Restate admin: ${RESTATE_ADMIN}"
echo "Sink: service://${SERVICE_NAME}/${SERVICE_HANDLER}"

wait_for_admin() {
    i=0
    while [ "$i" -lt "$MAX_RETRIES" ]; do
        if curl -sf "${RESTATE_ADMIN}/services" >/dev/null 2>&1; then
            echo "Restate is reachable"
            return 0
        fi
        i=$((i + 1))
        echo "Waiting for Restate (attempt ${i}/${MAX_RETRIES})"
        sleep "$RETRY_DELAY_SECONDS"
    done
    echo "Restate not reachable after ${MAX_RETRIES} attempts" >&2
    return 1
}

subscription_exists() {
    source_uri="$1"
    body=$(curl -sf "${RESTATE_ADMIN}/subscriptions") || return 1
    if command -v jq >/dev/null 2>&1; then
        printf '%s' "$body" | jq -e --arg s "$source_uri" \
            'if type == "array" then any(.[]; .source == $s) else (.subscriptions // []) | any(.[]; .source == $s) end' \
            >/dev/null
    else
        printf '%s' "$body" | grep -q "\"source\":\"${source_uri}\""
    fi
}

create_subscription() {
    topic="$1"
    source_uri="kafka://${KAFKA_CLUSTER}/${topic}"
    sink_uri="service://${SERVICE_NAME}/${SERVICE_HANDLER}"
    subscription_id="kafka-${KAFKA_CLUSTER}-$(printf '%s' "$topic" | tr '_' '-')"

    echo "Creating ${source_uri} -> ${sink_uri}"
    response=$(curl -sS -X POST "${RESTATE_ADMIN}/subscriptions" \
        -H "Content-Type: application/json" \
        -w '\nHTTP_STATUS:%{http_code}' \
        -d "{\"id\":\"${subscription_id}\",\"source\":\"${source_uri}\",\"sink\":\"${sink_uri}\",\"options\":{}}" \
        2>/dev/null) || true

    status=$(printf '%s' "$response" | sed -n 's/^HTTP_STATUS://p')
    body=$(printf '%s' "$response" | sed '/^HTTP_STATUS:/d')

    case "$status" in
        200|201)
            echo "  created"
            return 0
            ;;
        409)
            echo "  already exists (409)"
            return 0
            ;;
        *)
            echo "  failed: status=${status:-none} body=${body}" >&2
            return 1
            ;;
    esac
}

wait_for_admin || exit 1

success=0
skipped=0
failed=0

for topic in $TOPICS; do
    [ -z "$topic" ] && continue
    source_uri="kafka://${KAFKA_CLUSTER}/${topic}"
    if subscription_exists "$source_uri"; then
        echo "Skipping ${source_uri}: already subscribed"
        skipped=$((skipped + 1))
    elif create_subscription "$topic"; then
        success=$((success + 1))
    else
        failed=$((failed + 1))
    fi
done

echo
echo "=== Summary ==="
echo "created=${success} skipped=${skipped} failed=${failed}"
[ "$failed" -eq 0 ] || exit 1
