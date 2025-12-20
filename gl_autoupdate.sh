cat << 'EOF' > /usr/bin/gl_autoupdate.sh
#!/bin/sh

# ==============================================================================
# GL.iNet MT6000 Auto-Updater (Target: SNAPSHOT Channel)
# Model: mt6000 (Proprietary/Stock)
# Method: Pure Shell/AWK (No jsonfilter/lua dependencies)
# ==============================================================================

# --- Configuration ---
MODEL="mt6000"
API_URL="https://firmware-api.gl-inet.com/cloud-api/model/info?model=${MODEL}"
SCRIPT_PATH="/usr/bin/gl_autoupdate.sh"
TIMESTAMP_FILE="/etc/config/gl_last_update_ts"
SYSUPGRADE_CONF="/etc/sysupgrade.conf"
TMP_FIRMWARE="/tmp/firmware.bin"

# --- 1. Self-Preservation ---
# Ensure this script survives the upgrade
if ! grep -q "$SCRIPT_PATH" "$SYSUPGRADE_CONF"; then
    echo "[+] Adding script to sysupgrade preservation list..."
    echo "$SCRIPT_PATH" >> "$SYSUPGRADE_CONF"
fi
if ! grep -q "$TIMESTAMP_FILE" "$SYSUPGRADE_CONF"; then
    echo "[+] Adding timestamp to sysupgrade preservation list..."
    echo "$TIMESTAMP_FILE" >> "$SYSUPGRADE_CONF"
fi

# --- 2. Fetch Info ---
echo "[+] Fetching firmware info for ${MODEL}..."
command -v wget >/dev/null 2>&1 || { echo "Error: wget not found."; exit 1; }
JSON_DATA=$(wget -qO- "$API_URL")

if [ -z "$JSON_DATA" ]; then
    echo "Error: Failed to fetch data from API."
    exit 1
fi

# --- 3. Parse JSON using AWK (Robust Fallback) ---
# We use AWK to find the block containing "SNAPSHOT" and extract fields.
# 1. We replace newlines with spaces to handle JSON on one line or multiple.
# 2. We look for the pattern "stage":"SNAPSHOT" and capture values around it.

# Extract Version
LATEST_VERSION=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | awk -F'"version":"' '{print $1}' | awk -F'"' '{print $2}' | head -n1)
# If version is empty, try looking 'before' the stage tag (depends on JSON order)
if [ -z "$LATEST_VERSION" ]; then
    LATEST_VERSION=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $1}' | awk -F'"version":"' '{print $NF}' | awk -F'"' '{print $1}')
fi

# Extract Compile Time
REMOTE_TIME=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | awk -F'"compile_time":' '{print $2}' | awk -F'[,}]' '{print $1}' | head -n1 | tr -d ' ')

# Extract Download Link
DOWNLOAD_URL=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | awk -F'"link":"' '{print $2}' | awk -F'"' '{print $1}' | head -n1)

# Extract SHA256
REMOTE_SHA256=$(echo "$JSON_DATA" | awk -F'"stage":"SNAPSHOT"' '{print $2}' | awk -F'"sha256":"' '{print $2}' | awk -F'"' '{print $1}' | head -n1)

# Validation
if [ -z "$LATEST_VERSION" ] || [ -z "$DOWNLOAD_URL" ]; then
    echo "Error: Could not parse SNAPSHOT version using AWK."
    echo "Debug Data: $JSON_DATA"
    exit 1
fi

echo "Channel:        SNAPSHOT"
echo "Remote Version: $LATEST_VERSION"
echo "Remote Build:   $REMOTE_TIME"

# --- 4. Check Local Timestamp ---
if [ -f "$TIMESTAMP_FILE" ]; then
    LOCAL_TIME=$(cat "$TIMESTAMP_FILE")
else
    LOCAL_TIME=0
fi
echo "Local Build:    $LOCAL_TIME"

# --- 5. Compare ---
if [ "$REMOTE_TIME" -le "$LOCAL_TIME" ]; then
    echo "[!] System is already up to date. Exiting."
    exit 0
fi

echo "[+] New SNAPSHOT found! Starting download..."

# --- 6. Download & Verify ---
wget -q -O "$TMP_FIRMWARE" "$DOWNLOAD_URL"
if [ ! -f "$TMP_FIRMWARE" ]; then
    echo "Error: Download failed."
    exit 1
fi

echo "[+] Verifying SHA256 checksum..."
LOCAL_SHA256=$(sha256sum "$TMP_FIRMWARE" | awk '{print $1}')

if [ "$LOCAL_SHA256" != "$REMOTE_SHA256" ]; then
    echo "CRITICAL ERROR: Checksum mismatch!"
    echo "Expected: $REMOTE_SHA256"
    echo "Actual:   $LOCAL_SHA256"
    rm "$TMP_FIRMWARE"
    exit 1
fi
echo "[+] Checksum verified."

# --- 7. Update Timestamp & Flash ---
echo "$REMOTE_TIME" > "$TIMESTAMP_FILE"

echo "[+] Starting sysupgrade..."
sysupgrade -v "$TMP_FIRMWARE"
EOF
