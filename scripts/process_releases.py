import os
import json
import requests
import subprocess
import datetime
from markdownify import markdownify as md

# --- Configuration ---
MODEL = os.getenv("MODEL", "mt6000")
API_URL = f"https://firmware-api.gl-inet.com/cloud-api/model/info?model={MODEL}"
HISTORY_FILE = "release_history.json"

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except:
            return []
    return []

def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)

def format_date(timestamp):
    return datetime.datetime.fromtimestamp(int(timestamp)).strftime('%Y-%m-%d %H:%M:%S UTC')

def clean_release_notes(html_notes):
    if not html_notes:
        return "No release notes provided."
    # Convert HTML to Markdown
    markdown = md(html_notes, heading_style="ATX")
    # Cleanup excessive newlines
    return markdown.strip()

def create_release(firmware):
    version = firmware['version']
    stage = firmware['stage'].upper() # SNAPSHOT, TESTING, RELEASE
    dl_info = firmware['download'][0]
    
    filename = dl_info['name']
    url = dl_info['link']
    sha256 = dl_info['sha256']
    compile_time = dl_info['compile_time']
    size_mb = round(dl_info['size'] / 1024 / 1024, 2)
    
    # 1. Generate Unique Tag
    # For RELEASE: v4.8.2
    # For OTHERS: v4.8.4-snapshot-1766075084 (Use compile time to handle daily snapshots of same version)
    if stage == "RELEASE":
        tag_name = f"v{version}"
        is_prerelease = False
        make_latest = "true"
    else:
        tag_name = f"v{version}-{stage.lower()}-{compile_time}"
        is_prerelease = True
        make_latest = "false"

    release_title = f"GL-MT6000 {stage.title()} {version}"
    if stage != "RELEASE":
        release_title += f" ({format_date(compile_time)})"

    # 2. formatting the Body
    notes = clean_release_notes(firmware.get('release_note', ''))
    
    body = f"""
# {release_title}

{notes}

---

### 📦 Firmware Details

| Property | Value |
| :--- | :--- |
| **Model** | {MODEL} |
| **Version** | {version} |
| **Channel** | {stage} |
| **Compile Time** | {format_date(compile_time)} |
| **File Size** | {size_mb} MB |
| **SHA256** | `{sha256}` |

### 🛡️ Verification
Verify the hash after downloading:
```bash
echo "{sha256} *{filename}" | sha256sum -c -
"""
    print(f"🚀 Processing: {tag_name}...")

    # 3. Download the File
    print(f"   Downloading {filename}...")
    r = requests.get(url, stream=True)
    if r.status_code == 200:
        with open(filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    else:
        print(f"❌ Failed to download {url}")
        return False

    # 4. Create Release via GH CLI
    cmd = [
        "gh", "release", "create", tag_name,
        filename, # Attach the file
        "--title", release_title,
        "--notes", body,
        "--repo", os.environ['GITHUB_REPOSITORY']
    ]

    if is_prerelease:
        cmd.append("--prerelease")

    # Explicitly handle 'latest' logic
    if make_latest == "true":
        cmd.append("--latest")
    else:
        # If it's a snapshot, ensure it doesn't accidentally become 'latest'
        cmd.append("--latest=false")

    try:
        subprocess.run(cmd, check=True)
        print(f"✅ Successfully released {tag_name}")
        
        # Cleanup file to save runner space
        os.remove(filename)
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to create release for {tag_name}: {e}")
        return False

def main():
    print(f"🔍 Fetching firmware info for {MODEL}...")
    try:
        resp = requests.get(API_URL)
        data = resp.json()
    except Exception as e:
        print(f"❌ API Error: {e}")
        exit(1)

    if 'info' not in data:
        print("❌ No info found in JSON")
        exit(1)

    # Sort data: Oldest first, so we release in chronological order on first run
    # This ensures the 'latest' release tag actually ends up on the newest one.
    firmwares = sorted(data['info'], key=lambda x: x['download'][0]['compile_time'])

    history = load_history()
    new_releases_count = 0

    for fw in firmwares:
        dl_info = fw['download'][0]
        unique_id = dl_info['sha256'] # SHA256 is the best unique identifier

        if unique_id in history:
            print(f"⏭️ Skipping {fw['version']} ({fw['stage']}) - Already released.")
            continue

        # Found a new one!
        success = create_release(fw)
        
        if success:
            history.append(unique_id)
            save_history(history) # Save immediately in case job crashes later
            new_releases_count += 1

    if new_releases_count == 0:
        print("🎉 System is up to date. No new firmware found.")
    else:
        print(f"🎉 Done! Processed {new_releases_count} new releases.")

if __name__ == "__main__":
    main()
