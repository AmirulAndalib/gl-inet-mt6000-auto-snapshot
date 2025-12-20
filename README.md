# GL.iNet MT6000 (Flint 2) Auto-Updater (Snapshot Channel)

A robust, self-preserving shell script to automatically update the GL.iNet GL-MT6000 (Flint 2) router to the latest **Snapshot** firmware. 

Unlike the default updater, this script specifically targets the `SNAPSHOT` channel (ignoring `TESTING` or `RELEASE`) and uses a dependency-free parsing method to ensure compatibility with all OpenWrt versions.

## 🚀 Features

* **Snapshot Targeting:** Explicitly filters for firmware staged as `SNAPSHOT`.
* **Zero Dependencies:** Uses pure `awk` and `sh`. Does **not** require `jsonfilter`, `lua`, `python`, or `curl`. It works on the most minimal BusyBox environments.
* **Self-Preserving:** Automatically adds itself and its timestamp file to `/etc/sysupgrade.conf` before flashing. The script survives the upgrade process.
* **Security:** Verifies the SHA256 checksum of the downloaded firmware before flashing.
* **Safety Checks:** Compares the remote compile time against the local timestamp to prevent re-flashing the same version.

## 📋 Prerequisites

* **Router:** GL.iNet GL-MT6000 (Flint 2).
* **Firmware:** Stock GL.iNet firmware or OpenWrt (Snapshot/Release).
* **SSH Access:** You must be able to SSH into your router.

## 🛠️ Installation

### Option 1: One-Line Install
Run this command in your router's terminal to download and install the script automatically:

```bash
wget -O /usr/bin/gl_autoupdate.sh https://raw.githubusercontent.com/AmirulAndalib/gl-inet-mt6000-auto-snapshot/main/gl_autoupdate.sh && chmod +x /usr/bin/gl_autoupdate.sh

```

### Option 2: Manual Install

1. SSH into your router:
```bash
ssh root@192.168.8.1

```


2. Create the script file:
```bash
vi /usr/bin/gl_autoupdate.sh

```


*(Paste the script content into this file and save)*
3. Make it executable:
```bash
chmod +x /usr/bin/gl_autoupdate.sh

```



## ⏰ Scheduling (Cron)

To have the router check for updates automatically every morning (e.g., at 5:00 AM), add a cron job.

Run this command in your SSH terminal to add the schedule automatically:

```bash
(crontab -l 2>/dev/null; echo "0 5 * * * /usr/bin/gl_autoupdate.sh >> /tmp/autoupdate.log 2>&1") | crontab -

```

### Verification

You can check if the job is added by running:

```bash
crontab -l

```

## 🖥️ Usage

To run the updater manually (for testing):

```bash
/usr/bin/gl_autoupdate.sh

```

**Output explanation:**

* `[+] Fetching firmware info...`: Connecting to GL.iNet API.
* `Channel: SNAPSHOT`: Confirms it found a snapshot version.
* `[!] System is already up to date`: No new version found.
* `[+] New SNAPSHOT found!`: Downloads, verifies checksum, and initiates `sysupgrade`. **The router will reboot.**

## ⚙️ How It Works (Technical)

1. **API Call:** Uses `wget` to fetch JSON data from `https://firmware-api.gl-inet.com`.
2. **Parsing:** Uses `awk` to treat the JSON as a string stream. It hunts specifically for the `"stage":"SNAPSHOT"` block and extracts the `version`, `compile_time`, `link`, and `sha256` fields. This avoids errors caused by missing Lua libraries or incompatible `jsonfilter` versions found in some snapshots.
3. **Persistence:** Before running `sysupgrade`, it checks `/etc/sysupgrade.conf`. If the script path or the timestamp file path is missing, it appends them. This tells OpenWrt to keep these files during the flash.

## ⚠️ Disclaimer

**Use at your own risk.** This script performs a `sysupgrade` which flashes the firmware of your router. While it includes checksum verification, flashing firmware always carries a small risk.

* This script is intended for the **GL-MT6000**.
* Snapshot firmware is bleeding-edge and may contain bugs.
