#!/usr/bin/env bash
# File: ops/install-recording.sh
# Summary: Install the stable Jibri recorder and a matching Chrome/ChromeDriver pair.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# Package configuration is preserved because this host already serves an existing Jitsi installation.
apt-get update -qq
apt-get install -y -o Dpkg::Options::="--force-confold" \
  jibri ffmpeg alsa-utils unzip jq python3-venv python3-pip fonts-noto-cjk

# Chrome and its driver are obtained only from Google's official distribution endpoints.
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
  | gpg --dearmor --yes -o /usr/share/keyrings/google-chrome-keyring.gpg
printf '%s\n' 'deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome-keyring.gpg] https://dl.google.com/linux/chrome/deb/ stable main' \
  > /etc/apt/sources.list.d/google-chrome.list
apt-get update -qq
apt-get install -y google-chrome-stable
chrome_version=$(google-chrome --product-version)
driver_url=$(curl -fsSL https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json \
  | jq -r --arg version "$chrome_version" '.versions[] | select(.version == $version) | .downloads.chromedriver[] | select(.platform == "linux64") | .url' | tail -1)
test -n "$driver_url"
work_dir=$(mktemp -d /tmp/jitsi-browser.XXXXXX)
trap 'rm -f "$work_dir/driver.zip"; rm -rf "$work_dir/chromedriver-linux64"; rmdir "$work_dir"' EXIT
curl -fsSL "$driver_url" -o "$work_dir/driver.zip"
unzip -q "$work_dir/driver.zip" -d "$work_dir"
install -m 755 "$work_dir/chromedriver-linux64/chromedriver" /usr/local/bin/chromedriver
# Update Chrome and the driver together using this script, as required by Jibri.
apt-mark hold google-chrome-stable
install -d /etc/opt/chrome/policies/managed
printf '%s\n' '{"CommandLineFlagSecurityWarningsEnabled":false}' \
  > /etc/opt/chrome/policies/managed/jitsi.json

# The existing virtual machine has the loopback module; keep it available after reboot.
modprobe snd_aloop
printf '%s\n' snd_aloop > /etc/modules-load.d/jibri.conf
usermod -aG adm,audio,video,plugdev jibri
install -d -o jibri -g jibri -m 750 /srv/jitsi-recordings
echo 'RECORDING_PACKAGES_READY'
google-chrome --product-version
chromedriver --version
