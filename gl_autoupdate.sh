#!/bin/sh
# ------------------------------------------------------------------------------
# GL.iNet MT6000 Firmware Auto-Updater
# Target: SNAPSHOT Channel
# Model: GL-MT6000 (Flint 2)
#
# Usage: gl_autoupdate.sh [-n]
#   -n   Dry run: check, download and verify, but do not flash.
#        DRY_RUN=1 in the environment does the same.
# ------------------------------------------------------------------------------

MODEL="mt6000"
API_URL="https://firmware-api.gl-inet.com/cloud-api/model/info?model=${MODEL}"
SCRIPT_PATH="/usr/bin/gl_autoupdate.sh"
TIMESTAMP_FILE="${TIMESTAMP_FILE:-/etc/config/gl_last_update_ts}"
SYSUPGRADE_CONF="${SYSUPGRADE_CONF:-/etc/sysupgrade.conf}"
TMP_FIRMWARE="${TMP_FIRMWARE:-/tmp/firmware.bin}"
LOCK_DIR="${LOCK_DIR:-/tmp/gl_autoupdate.lock}"
RETRIES=3
RETRY_DELAY=10
WGET_TIMEOUT=60

[ "$1" = "-n" ] && DRY_RUN=1
DRY_RUN="${DRY_RUN:-0}"

log() {
    # $1 = level, rest = message. Echo for the terminal/cron log, and send to
    # syslog when logger is available.
    level="$1"
    shift
    echo "[$level] $*"
    if command -v logger >/dev/null 2>&1; then
        logger -t gl_autoupdate "[$level] $*"
    fi
}

die() {
    log ERROR "$*"
    exit 1
}

require() {
    for cmd in "$@"; do
        command -v "$cmd" >/dev/null 2>&1 || die "Required command not found: $cmd"
    done
}

# fetch URL OUTPUT_FILE, retrying on failure
fetch() {
    attempt=1
    while [ "$attempt" -le "$RETRIES" ]; do
        if wget -q -T "$WGET_TIMEOUT" -O "$2" "$1" && [ -s "$2" ]; then
            return 0
        fi
        log WARN "Download attempt $attempt/$RETRIES failed: $1"
        attempt=$((attempt + 1))
        [ "$attempt" -le "$RETRIES" ] && sleep "$RETRY_DELAY"
    done
    rm -f "$2"
    return 1
}

if [ "$DRY_RUN" = "1" ]; then
    require wget sha256sum awk
else
    require wget sha256sum awk sysupgrade
fi

# Only one instance at a time. mkdir is atomic, unlike test-then-touch.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    die "Another instance is running (remove $LOCK_DIR if it is stale)"
fi
trap 'rm -rf "$LOCK_DIR"' EXIT
trap 'exit 1' INT TERM

# Ensure script and timestamp survive upgrades
if [ "$DRY_RUN" != "1" ]; then
    if ! grep -qxF "$SCRIPT_PATH" "$SYSUPGRADE_CONF" 2>/dev/null; then
        log INFO "Adding script to sysupgrade preservation list"
        echo "$SCRIPT_PATH" >> "$SYSUPGRADE_CONF"
    fi
    if ! grep -qxF "$TIMESTAMP_FILE" "$SYSUPGRADE_CONF" 2>/dev/null; then
        log INFO "Adding timestamp to sysupgrade preservation list"
        echo "$TIMESTAMP_FILE" >> "$SYSUPGRADE_CONF"
    fi
fi

# Fetch firmware info
log INFO "Fetching firmware info for ${MODEL}..."
JSON_FILE="${TMP_FIRMWARE}.json"
fetch "$API_URL" "$JSON_FILE" || die "Failed to fetch data from API"
JSON_DATA=$(cat "$JSON_FILE")
rm -f "$JSON_FILE"

# Parse JSON. The API returns one line, and each firmware object looks like:
# { "version": "4.8.4", "stage": "SNAPSHOT", ... "download": [{ "compile_time": X, "link": "...", "sha256": "..." }] }
# Version comes BEFORE stage, download details come AFTER.

# Version: last "version" before the SNAPSHOT marker
LATEST_VERSION=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $1}' | awk -F'"version":"' '{print $NF}' | awk -F'"' '{print $1}')

# Download details: first match after the SNAPSHOT marker
AFTER_MARKER=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $2}')
REMOTE_TIME=$(echo "$AFTER_MARKER" | awk -F'"compile_time":' '{print $2}' | awk -F'[,}]' '{print $1}' | head -n1 | tr -d ' ')
DOWNLOAD_URL=$(echo "$AFTER_MARKER" | awk -F'"link":"' '{print $2}' | awk -F'"' '{print $1}' | head -n1)
REMOTE_SHA256=$(echo "$AFTER_MARKER" | awk -F'"sha256":"' '{print $2}' | awk -F'"' '{print $1}' | head -n1)

# Validate parsed data
if [ -z "$AFTER_MARKER" ] || [ -z "$LATEST_VERSION" ] || [ -z "$DOWNLOAD_URL" ] || [ -z "$REMOTE_SHA256" ]; then
    log ERROR "Failed to parse SNAPSHOT firmware data"
    echo "[DEBUG] Version: $LATEST_VERSION"
    echo "[DEBUG] URL: $DOWNLOAD_URL"
    echo "[DEBUG] SHA256: $REMOTE_SHA256"
    exit 1
fi

case "$REMOTE_TIME" in
    ''|*[!0-9]*) die "Failed to parse compile time: '$REMOTE_TIME'" ;;
esac

echo "Channel:        SNAPSHOT"
echo "Remote Version: $LATEST_VERSION"
echo "Remote Build:   $REMOTE_TIME"

# Read local timestamp; anything non-numeric counts as "never updated"
LOCAL_TIME=0
if [ -f "$TIMESTAMP_FILE" ]; then
    LOCAL_TIME=$(tr -d '[:space:]' < "$TIMESTAMP_FILE")
    case "$LOCAL_TIME" in
        ''|*[!0-9]*) LOCAL_TIME=0 ;;
    esac
fi
echo "Local Build:    $LOCAL_TIME"

if [ "$REMOTE_TIME" -le "$LOCAL_TIME" ]; then
    log INFO "System is up to date"
    exit 0
fi

log INFO "New SNAPSHOT firmware available, starting download..."

fetch "$DOWNLOAD_URL" "$TMP_FIRMWARE" || die "Download failed"

log INFO "Verifying SHA256 checksum..."
LOCAL_SHA256=$(sha256sum "$TMP_FIRMWARE" | awk '{print $1}')

if [ "$LOCAL_SHA256" != "$REMOTE_SHA256" ]; then
    log ERROR "Checksum mismatch"
    log ERROR "Expected: $REMOTE_SHA256"
    log ERROR "Actual:   $LOCAL_SHA256"
    rm -f "$TMP_FIRMWARE"
    exit 1
fi
log INFO "Checksum verified"

if [ "$DRY_RUN" = "1" ]; then
    log INFO "Dry run: would flash $TMP_FIRMWARE (build $REMOTE_TIME)"
    rm -f "$TMP_FIRMWARE"
    exit 0
fi

# The timestamp has to be written before flashing so it is carried over into
# the new image. sysupgrade either hands off to procd and returns 0 while the
# flash continues in the background, or reboots without returning. A non-zero
# exit means the image was rejected: put the old value back so the next run
# tries again.
echo "$REMOTE_TIME" > "$TIMESTAMP_FILE"

log INFO "Starting system upgrade"
sysupgrade -v "$TMP_FIRMWARE"
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
    echo "$LOCAL_TIME" > "$TIMESTAMP_FILE"
    rm -f "$TMP_FIRMWARE"
    die "sysupgrade failed with exit code $STATUS"
fi
log INFO "sysupgrade accepted the image, the router will reboot"
