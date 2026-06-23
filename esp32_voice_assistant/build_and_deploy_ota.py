#!/usr/bin/env python3
"""
build_and_deploy_ota.py — Build ESP32 firmware & deploy OTA update.

Usage:
    python build_and_deploy_ota.py              # Auto: build + latest version
    python build_and_deploy_ota.py --version 1.1.0  # Specify version

Prerequisites:
    - PlatformIO CLI (pio) installed and in PATH
    - Server SSH access configured (optional, for remote deploy)

Workflow:
    1. Update FIRMWARE_VERSION in the source
    2. Build firmware with PlatformIO
    3. Copy .bin to server/firmware/
    4. Update manifest (done dynamically by server endpoint)
"""

import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
INO_FILE = os.path.join(PROJECT_DIR, "esp32_voice_assistant.ino")
CONFIG_H = os.path.join(PROJECT_DIR, "config.h")
FIRMWARE_DIR = os.path.join(PROJECT_DIR, "server", "firmware")
PLATFORMIO_INI = os.path.join(PROJECT_DIR, "platformio.ini")


def get_current_version() -> str:
    """Read the current FIRMWARE_VERSION from config.h."""
    with open(CONFIG_H, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(r'#define\s+FIRMWARE_VERSION\s+"(.+?)"', content)
    if not match:
        print("ERROR: FIRMWARE_VERSION not found in config.h")
        sys.exit(1)
    return match.group(1)


def set_version(version: str):
    """Update FIRMWARE_VERSION in config.h."""
    with open(CONFIG_H, "r", encoding="utf-8") as f:
        content = f.read()
    content = re.sub(
        r'#define\s+FIRMWARE_VERSION\s+"(.+?)"',
        f'#define FIRMWARE_VERSION       "{version}"',
        content,
    )
    # Also update platformio.ini build flag
    with open(PLATFORMIO_INI, "r", encoding="utf-8") as f:
        pio = f.read()
    pio = re.sub(
        r'-DFIRMWARE_VERSION="(.+?)"',
        f'-DFIRMWARE_VERSION="{version}"',
        pio,
    )
    with open(PLATFORMIO_INI, "w", encoding="utf-8") as f:
        f.write(pio)
    print(f"  Updated version to {version}")


def build():
    """Run PlatformIO build."""
    print("  Building firmware...")
    result = subprocess.run(
        ["pio", "run"],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("  BUILD FAILED:")
        print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
        print(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
        sys.exit(1)
    # Find the built .bin
    bin_files = glob.glob(
        os.path.join(PROJECT_DIR, ".pio", "build", "**", "*.bin"), recursive=True
    )
    # We want the firmware binary, typically named firmware.bin
    firmware_bin = None
    for f in bin_files:
        if "firmware" in os.path.basename(f).lower():
            firmware_bin = f
    if not firmware_bin:
        # Fallback: look for merged binary
        merged = os.path.join(
            PROJECT_DIR, ".pio", "build", "esp32-s3-n16r8", "firmware.bin"
        )
        if os.path.exists(merged):
            firmware_bin = merged
    if not firmware_bin:
        print("  ERROR: Could not find firmware.bin in build output")
        print("  Searched:")
        for f in bin_files:
            print(f"    - {f}")
        sys.exit(1)
    return firmware_bin


def deploy(firmware_bin: str, version: str):
    """Copy firmware to server/firmware/ with versioned filename."""
    os.makedirs(FIRMWARE_DIR, exist_ok=True)
    dest = os.path.join(FIRMWARE_DIR, f"firmware_v{version}.bin")
    shutil.copy2(firmware_bin, dest)
    
    # Also copy as firmware_latest.bin for easy reference
    latest = os.path.join(FIRMWARE_DIR, "firmware_latest.bin")
    shutil.copy2(firmware_bin, latest)
    
    file_size = os.path.getsize(dest)
    md5 = ""
    with open(dest, "rb") as f:
        md5 = hashlib.md5(f.read()).hexdigest()
    
    print(f"  Deployed: {dest}")
    print(f"  Latest:   {latest}")
    print(f"  Size:     {file_size:,} bytes")
    print(f"  MD5:      {md5}")
    print(f"  Manifest: http://129.204.166.180:8000/firmware/manifest.json")
    print()
    print("  ESP32 will detect the update automatically within 6 hours,")
    print("  or you can double-click the button to check immediately,")
    print("  or send 'OTA:version' over WebSocket from the server.")


def main():
    parser = argparse.ArgumentParser(description="Build & deploy OTA firmware update")
    parser.add_argument("--version", "-v", help="New version string (e.g. 1.1.0)")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be done"
    )
    args = parser.parse_args()

    current = get_current_version()
    version = args.version or current
    print(f"Current version: {current}")

    if args.dry_run:
        print(f"[DRY-RUN] Would build version {version}")
        return

    if version != current:
        print(f"Updating version: {current} -> {version}")
        set_version(version)
    else:
        print(f"Building version {version} (unchanged)")

    firmware_bin = build()
    print(f"  Built: {firmware_bin} ({os.path.getsize(firmware_bin):,} bytes)")

    deploy(firmware_bin, version)

    print()
    print("To trigger update on device:")
    print("  1. Double-click the button on the doll")
    print("  2. Or send via WebSocket: OTA:version")
    print("  3. Or wait for auto-check (up to 6 hours)")


if __name__ == "__main__":
    main()
