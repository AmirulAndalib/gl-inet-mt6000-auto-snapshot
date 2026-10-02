# GL.iNet MT6000 (Flint 2) Auto-Updater

Automated firmware update system for the GL.iNet GL-MT6000 (Flint 2) router, targeting the SNAPSHOT release channel.

## Overview

This project provides a lightweight, self-preserving shell script that automatically updates your GL-MT6000 router to the latest SNAPSHOT firmware. The script is designed to work reliably on minimal BusyBox environments without external dependencies.

## Features

- **SNAPSHOT Channel Targeting**: Explicitly filters for firmware marked as SNAPSHOT, ignoring TESTING and RELEASE channels
- **Zero Dependencies**: Pure POSIX shell and AWK implementation; no requirement for jsonfilter, lua, python, or curl
- **Self-Preserving**: Automatically registers itself in `/etc/sysupgrade.conf` to survive firmware upgrades
- **Checksum Verification**: Validates SHA256 checksums before flashing to prevent corrupted firmware installation
- **Duplicate Prevention**: Tracks installed firmware timestamps to avoid unnecessary re-flashing
- **Safe Retry on Failure**: If `sysupgrade` rejects the image, the stored timestamp is restored so the next run tries again
- **Retry Logic**: API requests and downloads are retried up to 3 times
- **Lock Protection**: An atomic lock directory (`/tmp/gl_autoupdate.lock`) prevents concurrent runs
- **System Logging**: Messages go to stdout and to syslog (tag `gl_autoupdate`)
- **Dry Run**: `-n` checks, downloads and verifies without flashing

## Requirements

- GL.iNet GL-MT6000 (Flint 2) router
- Stock GL.iNet firmware or OpenWrt
- SSH access to the router
- BusyBox with wget, sha256sum, and awk

## Installation

### Quick Install

Run this command via SSH on your router:

```bash
wget -O /usr/bin/gl_autoupdate.sh https://raw.githubusercontent.com/AmirulAndalib/gl-inet-mt6000-auto-snapshot/main/gl_autoupdate.sh && chmod +x /usr/bin/gl_autoupdate.sh
```

### Manual Install

1. Connect to your router via SSH:

   ```bash
   ssh root@192.168.8.1
   ```

2. Create the script file:

   ```bash
   vi /usr/bin/gl_autoupdate.sh
   ```

3. Paste the script content and save the file.

4. Set executable permissions:

   ```bash
   chmod +x /usr/bin/gl_autoupdate.sh
   ```

## Usage

### Manual Execution

Run the updater manually to check for and apply updates:

```bash
/usr/bin/gl_autoupdate.sh
```

### Dry Run

Check for an update, download it and verify the checksum without flashing:

```bash
/usr/bin/gl_autoupdate.sh -n
```

### Scheduled Updates

To automatically check for updates daily at 5:00 AM, add a cron job:

```bash
(crontab -l 2>/dev/null; echo "0 5 * * * /usr/bin/gl_autoupdate.sh >> /tmp/autoupdate.log 2>&1") | crontab -
```

Verify the cron job was added:

```bash
crontab -l
```

### Output Reference

| Message | Description |
| :--- | :--- |
| `[INFO] Fetching firmware info...` | Querying the GL.iNet firmware API |
| `Channel: SNAPSHOT` | Confirmed SNAPSHOT channel firmware found |
| `[INFO] System is up to date` | No newer firmware available |
| `[INFO] New SNAPSHOT firmware available, starting download...` | Update detected; download starting |
| `[INFO] Starting system upgrade` | Firmware verified; initiating sysupgrade |
| `[ERROR] Another instance is running` | Lock held by another run; remove `/tmp/gl_autoupdate.lock` if stale |

## Technical Details

### API Integration

The script queries the GL.iNet firmware API at:

```
https://firmware-api.gl-inet.com/cloud-api/model/info?model=mt6000
```

### JSON Parsing

The script uses AWK-based string parsing to extract firmware information from the API response. This approach ensures compatibility with minimal BusyBox environments that lack jsonfilter or lua.

Extracted fields:
- `version`: Firmware version string
- `compile_time`: Build timestamp (Unix epoch)
- `link`: Download URL
- `sha256`: Checksum for verification
- `size`: File size in bytes

### Persistence Mechanism

Before initiating a firmware upgrade, the script ensures the following paths are listed in `/etc/sysupgrade.conf`:

- `/usr/bin/gl_autoupdate.sh`
- `/etc/config/gl_last_update_ts`

This ensures both the script and its state file survive the upgrade process.

## GitHub Actions Integration

The `GL.iNet MT6000 Firmware Release` workflow runs daily (and on demand) and:

1. Fetches the firmware list from the GL.iNet API (all channels: RELEASE, TESTING, SNAPSHOT)
2. Skips builds already published, matched by SHA256 against `release_history.json` and against the digests and file names of assets already on GitHub
3. Downloads each new build and checks its SHA256 and size against the API before uploading
4. Creates one GitHub release per build and records it in `release_history.json`

Tag scheme:

| Build | Tag |
| :--- | :--- |
| Stable release | `v<version>` |
| Stable respin (GL.iNet re-published the same version with a new binary) | `v<version>-<compile_time>` |
| Testing / snapshot | `v<version>-<stage>-<compile_time>` |

Only the build with the newest compile time in the API response is marked "Latest", so a late respin of an old version cannot take it over.

To preview what a run would do, start the workflow manually with **dry_run** checked, or run locally:

```bash
DRY_RUN=1 GITHUB_REPOSITORY=<owner>/<repo> GH_TOKEN=$(gh auth token) python scripts/process_releases.py
```

A second `CI` workflow lints and tests both scripts, including the router script under BusyBox `ash`.

## File Structure

```
gl-inet-mt6000-auto-snapshot/
├── gl_autoupdate.sh                # Router-side auto-update script
├── release_history.json            # SHA256 of every published build
├── scripts/
│   └── process_releases.py         # GitHub release automation
├── tests/
│   ├── test_process_releases.py    # pytest suite for the release script
│   └── test_gl_autoupdate.sh       # Router script tests with fake wget/sysupgrade
├── .github/workflows/
│   ├── glinet_release.yml          # Daily release job
│   └── ci.yml                      # Lint and tests
└── README.md
```

## Security Considerations

- All firmware downloads are verified using SHA256 checksums
- The script only executes sysupgrade after successful verification
- Failed downloads or checksum mismatches abort the update process
- A lock directory prevents concurrent executions on the router
- The release workflow verifies every download against the API checksum before publishing it

## Troubleshooting

### Script fails with "Required command not found"

Ensure your firmware includes the required utilities:

```bash
which wget sha256sum awk sysupgrade
```

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
ruff check scripts tests
shellcheck -s sh gl_autoupdate.sh tests/test_gl_autoupdate.sh
sh tests/test_gl_autoupdate.sh                               # or SHELL_UNDER_TEST="busybox ash"
```

### API request timeout

Check network connectivity and DNS resolution:

```bash
ping -c 3 firmware-api.gl-inet.com
```

### Checksum verification fails

This indicates a corrupted download. The script will automatically clean up and exit. Re-run the script to attempt the download again.

### View logs

Check the system log for detailed output:

```bash
logread | grep gl_autoupdate
```

Or check the cron log file:

```bash
cat /tmp/autoupdate.log
```

## License

This project is provided as-is for personal use. Use at your own risk.

## Disclaimer

- SNAPSHOT firmware is development-grade and may contain bugs or instability
- Always maintain backups of your router configuration
- The authors are not responsible for any damage resulting from firmware updates
- This project is not affiliated with GL.iNet
