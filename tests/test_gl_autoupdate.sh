#!/bin/sh
# shellcheck disable=SC2016,SC2034  # check() evals its single-quoted conditions on purpose
# Tests for gl_autoupdate.sh with fake wget/sysupgrade/logger on PATH.
# Usage: SHELL_UNDER_TEST="busybox ash" sh tests/test_gl_autoupdate.sh
# Defaults to plain sh. Nothing here touches a real router.

SH="${SHELL_UNDER_TEST:-sh}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT="$ROOT/gl_autoupdate.sh"
FAILS=0

setup() {
    WORK=$(mktemp -d)
    mkdir -p "$WORK/bin"
    printf 'firmware-bytes\n' > "$WORK/firmware.src"
    SHA=$(sha256sum "$WORK/firmware.src" | awk '{print $1}')
    # One line, same key order as the real API.
    printf '%s' '{"code":0,"info":[{"model":"mt6000","version":"4.11.0","stage":"RELEASE","release_note":"<p>x</p>","download":[{"compileTime":"x","link":"https://fw.example/rel.bin","name":"rel.bin","sha256":"aaaa","size":1,"compile_time":1700000000}]},{"model":"mt6000","version":"4.9.1","stage":"SNAPSHOT","release_note":"<p>\"version\":\"9.9.9\"</p>","download":[{"compileTime":"x","link":"https://fw.example/snap.bin","name":"snap.bin","sha256":"'"$SHA"'","size":15,"compile_time":1790872109}]}],"msg":""}' > "$WORK/api.json"

    cat > "$WORK/bin/wget" <<FAKE
#!/bin/sh
out=""; url=""
while [ \$# -gt 0 ]; do
    case "\$1" in
        -O) out="\$2"; shift 2 ;;
        -T) shift 2 ;;
        -q) shift ;;
        *) url="\$1"; shift ;;
    esac
done
echo "\$url" >> "$WORK/wget.log"
case "\$url" in
    *firmware-api*) cp "$WORK/api.json" "\$out" ;;
    *snap.bin) cp "\${FAKE_FIRMWARE:-$WORK/firmware.src}" "\$out" ;;
    *) exit 1 ;;
esac
FAKE
    cat > "$WORK/bin/sysupgrade" <<FAKE
#!/bin/sh
echo "sysupgrade \$*" >> "$WORK/sysupgrade.log"
exit \${FAKE_SYSUPGRADE_RC:-0}
FAKE
    printf '#!/bin/sh\nexit 0\n' > "$WORK/bin/logger"
    printf '#!/bin/sh\nexit 0\n' > "$WORK/bin/sleep"
    chmod +x "$WORK/bin/"*
}

run() {
    PATH="$WORK/bin:$PATH" \
    TIMESTAMP_FILE="$WORK/ts" SYSUPGRADE_CONF="$WORK/sysupgrade.conf" \
    TMP_FIRMWARE="$WORK/fw.bin" LOCK_DIR="$WORK/lock" \
    $SH "$SCRIPT" "$@" > "$WORK/out.log" 2>&1
}

check() {
    if eval "$2"; then
        echo "ok   - $1"
    else
        echo "FAIL - $1"
        sed 's/^/       /' "$WORK/out.log"
        FAILS=$((FAILS + 1))
    fi
}

setup
run
check "fresh install flashes the snapshot" '[ $? -eq 0 ] && grep -q "sysupgrade -v $WORK/fw.bin" "$WORK/sysupgrade.log"'
check "timestamp stored" '[ "$(cat "$WORK/ts")" = 1790872109 ]'
check "parses snapshot version, not one inside release notes" 'grep -q "Remote Version: 4.9.1" "$WORK/out.log"'
check "registers files for preservation" 'grep -qx "$WORK/ts" "$WORK/sysupgrade.conf"'
check "lock released" '[ ! -d "$WORK/lock" ]'
rm -rf "$WORK"

setup
echo 1790872109 > "$WORK/ts"
run
check "up to date does nothing" '[ $? -eq 0 ] && grep -q "System is up to date" "$WORK/out.log" && [ ! -f "$WORK/sysupgrade.log" ]'
rm -rf "$WORK"

setup
echo 100 > "$WORK/ts"
FAKE_SYSUPGRADE_RC=1 run
rc=$?
check "failed sysupgrade exits non-zero" '[ $rc -ne 0 ]'
check "failed sysupgrade restores old timestamp" '[ "$(cat "$WORK/ts")" = 100 ]'
rm -rf "$WORK"

setup
printf 'corrupt\n' > "$WORK/firmware.bad"
FAKE_FIRMWARE="$WORK/firmware.bad" run
rc=$?
check "checksum mismatch aborts without flashing" '[ $rc -ne 0 ] && grep -q "Checksum mismatch" "$WORK/out.log" && [ ! -f "$WORK/sysupgrade.log" ] && [ ! -f "$WORK/ts" ]'
rm -rf "$WORK"

setup
run -n
check "dry run verifies but does not flash" '[ $? -eq 0 ] && grep -q "Dry run" "$WORK/out.log" && [ ! -f "$WORK/sysupgrade.log" ] && [ ! -f "$WORK/ts" ]'
rm -rf "$WORK"

setup
mkdir "$WORK/lock"
run
rc=$?
check "second instance refuses to run" '[ $rc -ne 0 ] && grep -q "Another instance" "$WORK/out.log" && [ -d "$WORK/lock" ]'
rm -rf "$WORK"

setup
printf '%s' '{"code":0,"info":[{"version":"4.11.0","stage":"RELEASE","download":[]}]}' > "$WORK/api.json"
run
rc=$?
check "no snapshot in API fails cleanly" '[ $rc -ne 0 ] && grep -q "Failed to parse SNAPSHOT" "$WORK/out.log"'
rm -rf "$WORK"

echo "failures: $FAILS"
[ "$FAILS" -eq 0 ]
