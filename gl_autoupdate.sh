#!/bin/sh
# ------------------------------------------------------------------------------
# GL.iNet MT6000 Firmware Auto-Updater
# Target: SNAPSHOT Channel
# Model: GL-MT6000 (Flint 2)
# ------------------------------------------------------------------------------

MODEL="mt6000"
API_URL="https://firmware-api.gl-inet.com/cloud-api/model/info?model=${MODEL}"
SCRIPT_PATH="/usr/bin/gl_autoupdate.sh"
TIMESTAMP_FILE="/etc/config/gl_last_update_ts"
SYSUPGRADE_CONF="/etc/sysupgrade.conf"
TMP_FIRMWARE="/tmp/firmware.bin"

# Ensure script and timestamp survive upgrades
if ! grep -qxF "$SCRIPT_PATH" "$SYSUPGRADE_CONF" 2>/dev/null; then
    echo "[INFO] Adding script to sysupgrade preservation list"
    echo "$SCRIPT_PATH" >> "$SYSUPGRADE_CONF"
fi
if ! grep -qxF "$TIMESTAMP_FILE" "$SYSUPGRADE_CONF" 2>/dev/null; then
    echo "[INFO] Adding timestamp to sysupgrade preservation list"
    echo "$TIMESTAMP_FILE" >> "$SYSUPGRADE_CONF"
fi

# Fetch firmware info
echo "[INFO] Fetching firmware info for ${MODEL}..."
command -v wget >/dev/null 2>&1 || { echo "[ERROR] wget not found"; exit 1; }

JSON_DATA=$(wget -qO- "$API_URL")
if [ -z "$JSON_DATA" ]; then
    echo "[ERROR] Failed to fetch data from API"
    exit 1
fi

# Parse JSON - The structure is:
# { "version": "4.8.4", "stage": "SNAPSHOT", ... "download": [{ "compile_time": X, "link": "...", "sha256": "..." }] }
# Version comes BEFORE stage, download details come AFTER

# Extract the SNAPSHOT block first (everything from version before SNAPSHOT to its download block)
# Get version - it appears BEFORE "stage":"SNAPSHOT" in the same object
LATEST_VERSION=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $1}' | awk -F'"version":"' '{print $NF}' | awk -F'"' '{print $1}')

# Get download block after SNAPSHOT marker - extract compile_time
REMOTE_TIME=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | awk -F'"compile_time":' '{print $2}' | awk -F'[,}]' '{print $1}' | head -n1 | tr -d ' ')

# Get download link
DOWNLOAD_URL=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | awk -F'"link":"' '{print $2}' | awk -F'"' '{print $1}' | head -n1)

# Get SHA256
REMOTE_SHA256=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | awk -F'"sha256":"' '{print $2}' | awk -F'"' '{print $1}' | head -n1)

# Validate parsed data
if [ -z "$LATEST_VERSION" ] || [ -z "$DOWNLOAD_URL" ] || [ -z "$REMOTE_SHA256" ]; then
    echo "[ERROR] Failed to parse SNAPSHOT firmware data"
    echo "[DEBUG] Version: $LATEST_VERSION"
    echo "[DEBUG] URL: $DOWNLOAD_URL"
    echo "[DEBUG] SHA256: $REMOTE_SHA256"
    exit 1
fi

if [ -z "$REMOTE_TIME" ]; then
    echo "[ERROR] Failed to parse compile time"
    exit 1
fi

echo "Channel:        SNAPSHOT"
echo "Remote Version: $LATEST_VERSION"
echo "Remote Build:   $REMOTE_TIME"

# Read local timestamp
if [ -f "$TIMESTAMP_FILE" ]; then
    LOCAL_TIME=$(cat "$TIMESTAMP_FILE" | tr -d '[:space:]')
else
    LOCAL_TIME=0
fi
echo "Local Build:    $LOCAL_TIME"

# Compare timestamps
if [ "$REMOTE_TIME" -le "$LOCAL_TIME" ] 2>/dev/null; then
    echo "[INFO] System is up to date"
    exit 0
fi

echo "[INFO] New SNAPSHOT found, starting download..."

# Download firmware
wget -q -O "$TMP_FIRMWARE" "$DOWNLOAD_URL"
if [ ! -s "$TMP_FIRMWARE" ]; then
    echo "[ERROR] Download failed"
    rm -f "$TMP_FIRMWARE"
    exit 1
fi

# Verify checksum
echo "[INFO] Verifying SHA256 checksum..."
LOCAL_SHA256=$(sha256sum "$TMP_FIRMWARE" | awk '{print $1}')

if [ "$LOCAL_SHA256" != "$REMOTE_SHA256" ]; then
    echo "[ERROR] Checksum mismatch"
    echo "[ERROR] Expected: $REMOTE_SHA256"
    echo "[ERROR] Actual:   $LOCAL_SHA256"
    rm -f "$TMP_FIRMWARE"
    exit 1
fi
echo "[INFO] Checksum verified"

# Update timestamp and flash
echo "$REMOTE_TIME" > "$TIMESTAMP_FILE"

echo "[INFO] Starting sysupgrade..."
sysupgrade -v "$TMP_FIRMWARE"
