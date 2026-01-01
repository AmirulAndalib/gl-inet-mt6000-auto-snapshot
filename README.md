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
- **Retry Logic**: Automatic retry mechanism for failed API requests and downloads
- **Lock File Protection**: Prevents concurrent execution of multiple update instances
- **System Logging**: Integrates with syslog for centralized logging and debugging

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
| `[INFO] New SNAPSHOT firmware available` | Update detected; download starting |
| `[INFO] Starting system upgrade` | Firmware verified; initiating sysupgrade |

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

This repository includes automated workflows that:

1. Periodically check for new firmware releases
2. Download and verify new firmware files
3. Create GitHub releases with attached firmware binaries
4. Maintain release history to prevent duplicates

The release processor script (`scripts/process_releases.py`) handles all release automation tasks.

## File Structure

```
gl-inet-mt6000-auto-snapshot/
├── gl_autoupdate.sh          # Router-side auto-update script
├── release_history.json      # Tracks processed firmware releases
├── scripts/
│   └── process_releases.py   # GitHub release automation
└── README.md
```

## Security Considerations

- All firmware downloads are verified using SHA256 checksums
- The script only executes sysupgrade after successful verification
- Failed downloads or checksum mismatches abort the update process
- Lock files prevent race conditions from concurrent executions

## Troubleshooting

### Script fails with "Required command not found"

Ensure your firmware includes the required utilities:

```bash
which wget sha256sum sysupgrade
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
