#!/bin/sh
# ------------------------------------------------------------------------------
# GL.iNet MT6000 Firmware Auto-Updater
# Target: SNAPSHOT Channel
# Model: GL-MT6000 (Flint 2)
# ------------------------------------------------------------------------------
# This script automatically updates the router to the latest snapshot firmware.
# It uses pure POSIX shell and AWK for maximum compatibility with minimal
# BusyBox environments. No external dependencies required.
# ------------------------------------------------------------------------------

set -e

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------
MODEL="mt6000"
API_URL="https://firmware-api.gl-inet.com/cloud-api/model/info?model=${MODEL}"
SCRIPT_PATH="/usr/bin/gl_autoupdate.sh"
TIMESTAMP_FILE="/etc/config/gl_last_update_ts"
SYSUPGRADE_CONF="/etc/sysupgrade.conf"
TMP_FIRMWARE="/tmp/firmware.bin"
LOG_TAG="gl_autoupdate"
LOCK_FILE="/tmp/gl_autoupdate.lock"
MAX_RETRIES=3
RETRY_DELAY=5

# ------------------------------------------------------------------------------
# Utility Functions
# ------------------------------------------------------------------------------

log_info() {
    logger -t "$LOG_TAG" -p info "$1" 2>/dev/null || true
    printf "[INFO] %s\n" "$1"
}

log_error() {
    logger -t "$LOG_TAG" -p err "$1" 2>/dev/null || true
    printf "[ERROR] %s\n" "$1" >&2
}

log_warn() {
    logger -t "$LOG_TAG" -p warning "$1" 2>/dev/null || true
    printf "[WARN] %s\n" "$1"
}

cleanup() {
    rm -f "$LOCK_FILE" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

check_dependencies() {
    for cmd in wget sha256sum sysupgrade; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            log_error "Required command '$cmd' not found"
            exit 1
        fi
    done
}

acquire_lock() {
    if [ -f "$LOCK_FILE" ]; then
        pid=$(cat "$LOCK_FILE" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            log_error "Another instance is running (PID: $pid)"
            exit 1
        fi
        log_warn "Removing stale lock file"
        rm -f "$LOCK_FILE"
    fi
    echo $$ > "$LOCK_FILE"
}

# ------------------------------------------------------------------------------
# Persistence Configuration
# ------------------------------------------------------------------------------

ensure_persistence() {
    local modified=0

    if ! grep -qxF "$SCRIPT_PATH" "$SYSUPGRADE_CONF" 2>/dev/null; then
        log_info "Adding script to sysupgrade preservation list"
        echo "$SCRIPT_PATH" >> "$SYSUPGRADE_CONF"
        modified=1
    fi

    if ! grep -qxF "$TIMESTAMP_FILE" "$SYSUPGRADE_CONF" 2>/dev/null; then
        log_info "Adding timestamp file to sysupgrade preservation list"
        echo "$TIMESTAMP_FILE" >> "$SYSUPGRADE_CONF"
        modified=1
    fi

    [ "$modified" -eq 1 ] && log_info "Preservation list updated"
    return 0
}

# ------------------------------------------------------------------------------
# API and JSON Parsing
# ------------------------------------------------------------------------------

fetch_firmware_info() {
    local attempt=1
    local json_data=""

    while [ "$attempt" -le "$MAX_RETRIES" ]; do
        log_info "Fetching firmware info for ${MODEL} (attempt $attempt/$MAX_RETRIES)"
        json_data=$(wget -qO- --timeout=30 "$API_URL" 2>/dev/null) || true

        if [ -n "$json_data" ]; then
            echo "$json_data"
            return 0
        fi

        log_warn "API request failed, retrying in ${RETRY_DELAY}s..."
        sleep "$RETRY_DELAY"
        attempt=$((attempt + 1))
    done

    log_error "Failed to fetch firmware data after $MAX_RETRIES attempts"
    return 1
}

parse_snapshot_field() {
    local json_data="$1"
    local field="$2"

    case "$field" in
        version)
            echo "$json_data" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | \
                awk -F'"version":"' '{print $1}' | awk -F'"' '{print $2}' | head -n1
            ;;
        compile_time)
            echo "$json_data" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | \
                awk -F'"compile_time":' '{print $2}' | awk -F'[,}]' '{print $1}' | \
                head -n1 | tr -d ' '
            ;;
        link)
            echo "$json_data" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | \
                awk -F'"link":"' '{print $2}' | awk -F'"' '{print $1}' | head -n1
            ;;
        sha256)
            echo "$json_data" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | \
                awk -F'"sha256":"' '{print $2}' | awk -F'"' '{print $1}' | head -n1
            ;;
        size)
            echo "$json_data" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | \
                awk -F'"size":' '{print $2}' | awk -F'[,}]' '{print $1}' | \
                head -n1 | tr -d ' '
            ;;
    esac
}

# ------------------------------------------------------------------------------
# Firmware Download and Verification
# ------------------------------------------------------------------------------

download_firmware() {
    local url="$1"
    local attempt=1

    while [ "$attempt" -le "$MAX_RETRIES" ]; do
        log_info "Downloading firmware (attempt $attempt/$MAX_RETRIES)"
        rm -f "$TMP_FIRMWARE"

        if wget -q --timeout=120 -O "$TMP_FIRMWARE" "$url" 2>/dev/null; then
            if [ -s "$TMP_FIRMWARE" ]; then
                return 0
            fi
        fi

        log_warn "Download failed, retrying in ${RETRY_DELAY}s..."
        sleep "$RETRY_DELAY"
        attempt=$((attempt + 1))
    done

    log_error "Failed to download firmware after $MAX_RETRIES attempts"
    return 1
}

verify_checksum() {
    local expected="$1"
    local actual

    log_info "Verifying SHA256 checksum"
    actual=$(sha256sum "$TMP_FIRMWARE" | awk '{print $1}')

    if [ "$actual" != "$expected" ]; then
        log_error "Checksum verification failed"
        log_error "Expected: $expected"
        log_error "Actual:   $actual"
        rm -f "$TMP_FIRMWARE"
        return 1
    fi

    log_info "Checksum verified successfully"
    return 0
}

check_disk_space() {
    local required_kb="$1"
    local available_kb

    available_kb=$(df /tmp 2>/dev/null | awk 'NR==2 {print $4}')

    if [ -n "$available_kb" ] && [ "$available_kb" -lt "$required_kb" ]; then
        log_error "Insufficient disk space: ${available_kb}KB available, ${required_kb}KB required"
        return 1
    fi

    return 0
}

# ------------------------------------------------------------------------------
# Main Update Logic
# ------------------------------------------------------------------------------

main() {
    local json_data remote_version remote_time download_url remote_sha256
    local remote_size local_time required_kb

    log_info "GL.iNet MT6000 Auto-Updater starting"

    check_dependencies
    acquire_lock
    ensure_persistence

    json_data=$(fetch_firmware_info) || exit 1

    remote_version=$(parse_snapshot_field "$json_data" "version")
    remote_time=$(parse_snapshot_field "$json_data" "compile_time")
    download_url=$(parse_snapshot_field "$json_data" "link")
    remote_sha256=$(parse_snapshot_field "$json_data" "sha256")
    remote_size=$(parse_snapshot_field "$json_data" "size")

    if [ -z "$remote_version" ] || [ -z "$download_url" ] || [ -z "$remote_sha256" ]; then
        log_error "Failed to parse SNAPSHOT firmware data"
        exit 1
    fi

    if [ -z "$remote_time" ] || ! echo "$remote_time" | grep -qE '^[0-9]+$'; then
        log_error "Invalid compile time: $remote_time"
        exit 1
    fi

    printf "\n"
    printf "Channel:        SNAPSHOT\n"
    printf "Remote Version: %s\n" "$remote_version"
    printf "Remote Build:   %s\n" "$remote_time"

    local_time=0
    if [ -f "$TIMESTAMP_FILE" ]; then
        local_time=$(cat "$TIMESTAMP_FILE" 2>/dev/null | tr -d '[:space:]')
        if ! echo "$local_time" | grep -qE '^[0-9]+$'; then
            local_time=0
        fi
    fi
    printf "Local Build:    %s\n" "$local_time"
    printf "\n"

    if [ "$remote_time" -le "$local_time" ]; then
        log_info "System is up to date"
        exit 0
    fi

    log_info "New SNAPSHOT firmware available"

    if [ -n "$remote_size" ]; then
        required_kb=$((remote_size / 1024 + 10240))
        check_disk_space "$required_kb" || exit 1
    fi

    download_firmware "$download_url" || exit 1
    verify_checksum "$remote_sha256" || exit 1

    echo "$remote_time" > "$TIMESTAMP_FILE"

    log_info "Starting system upgrade"
    rm -f "$LOCK_FILE"
    exec sysupgrade -v "$TMP_FIRMWARE"
}

main "$@"