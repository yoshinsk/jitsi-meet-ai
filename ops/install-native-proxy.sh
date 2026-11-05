#!/usr/bin/env bash
# File: ops/install-native-proxy.sh
# Summary: Build the pinned official Jitsi Opus transcription/translation proxy under a credential-isolated account.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
proxy_commit=ee5836e67806349395ede2447376003da893f7a9
apt-get install -y git curl xz-utils build-essential python3-dev
if ! id jitsi-proxy-build >/dev/null 2>&1; then
  useradd --system --user-group --create-home --home-dir /var/lib/jitsi-proxy-build --shell /usr/sbin/nologin jitsi-proxy-build
fi

# Keep this LTS runtime local to the translation service rather than replacing the system's Node executable.
if [ ! -x /opt/jitsi-node/bin/node ]; then
  work_dir=$(mktemp -d /tmp/jitsi-native-node.XXXXXX)
  curl -fsSL https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt -o "$work_dir/SHASUMS256.txt"
  node_archive=$(awk '$2 ~ /^node-v24\.[0-9]+\.[0-9]+-linux-x64.tar.xz$/ {print $2}' "$work_dir/SHASUMS256.txt")
  test -n "$node_archive"
  curl -fsSL "https://nodejs.org/dist/latest-v24.x/$node_archive" -o "$work_dir/$node_archive"
  (cd "$work_dir" && grep " $node_archive\$" SHASUMS256.txt | sha256sum -c -)
  install -d -m 755 /opt/jitsi-node
  tar -xJf "$work_dir/$node_archive" --strip-components=1 -C /opt/jitsi-node
  rm -f "$work_dir/SHASUMS256.txt" "$work_dir/$node_archive"
  rmdir "$work_dir"
fi
install -d -o jitsi-proxy-build -g jitsi-proxy-build -m 755 /opt/jitsi-opus-proxy
# A repeat installation must give the isolated builder ownership before touching its pinned checkout.
chown -R jitsi-proxy-build:jitsi-proxy-build /opt/jitsi-opus-proxy
cd /opt/jitsi-opus-proxy
if [ ! -d .git ]; then
  runuser -u jitsi-proxy-build -- git init -q
  runuser -u jitsi-proxy-build -- git remote add origin https://github.com/jitsi/opus-transcriber-proxy.git
fi
runuser -u jitsi-proxy-build -- git fetch -q --depth 1 origin "$proxy_commit"
runuser -u jitsi-proxy-build -- git checkout -q --detach FETCH_HEAD
runuser -u jitsi-proxy-build -- git submodule update --init --depth 1
proxy_path=/opt/jitsi-node/bin:/usr/local/bin:/usr/bin:/bin
# Third-party package lifecycle scripts are disabled; the reviewed native and bundle build commands run explicitly.
runuser -u jitsi-proxy-build -- env PATH="$proxy_path" npm ci --ignore-scripts --no-audit --no-fund
runuser -u jitsi-proxy-build -- env PATH="$proxy_path" npm run build:native -- --jobs=2
chown -R root:root /opt/jitsi-opus-proxy
chmod -R go-w /opt/jitsi-opus-proxy
# Apply the reviewed OpenAI adapter and preserve lazy optional codecs before building the runtime bundle.
python3 /opt/jitsi-ai/ops/patch-opus-proxy.py
echo NATIVE_PROXY_BUILT
/opt/jitsi-node/bin/node --version
