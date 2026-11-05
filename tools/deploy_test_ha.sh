#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Paul Fryer
# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Deploy the working tree to a local test Home Assistant OS host over SSH.
#
# Until pyronix-cloud is on PyPI, HA cannot download the library, so this builds a
# wheel with a unique local version (0.1.0+dev.<time>.g<sha>), copies it to
# /config/pyronix_wheels/ and points the host's copy of manifest.json at it. A new
# wheel URL on every run makes HA's `uv pip install --upgrade` really reinstall.
#
# Usage: tools/deploy_test_ha.sh [--no-restart] [user@host]
#   Host defaults to $PYRONIX_HA_HOST, then PYRONIX_HA_HOST in .env.
#   Needs the "Advanced SSH & Web Terminal" app (it provides the `ha` CLI).
#
# Restarting HA reloads any configured entry, which logs in to the real panel.
set -euo pipefail

restart=1
host=""
for arg in "$@"; do
    case "$arg" in
        --no-restart) restart=0 ;;
        -h | --help) sed -n '5,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) host="$arg" ;;
    esac
done

repo="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo"

# Read only the host key from .env; never source it (it holds panel secrets).
if [[ -z "$host" ]]; then
    host="${PYRONIX_HA_HOST:-}"
fi
if [[ -z "$host" && -f .env ]]; then
    host="$(sed -n 's/^PYRONIX_HA_HOST=//p' .env | tail -1 | tr -d "\"'")"
fi
if [[ -z "$host" ]]; then
    echo "no host: pass user@host, or set PYRONIX_HA_HOST (env or .env)" >&2
    exit 2
fi

ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10)
# shellcheck disable=SC2029  # remote commands are built locally on purpose
remote() { ssh "${ssh_opts[@]}" "$host" "$@"; }

base_version="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
sha="$(git rev-parse --short HEAD)"
dirty=""
git diff --quiet HEAD -- src custom_components pyproject.toml || dirty=".dirty"
version="${base_version}+dev.$(date -u +%Y%m%d%H%M%S).g${sha}${dirty}"
wheel="pyronix_cloud-${version}-py3-none-any.whl"
wheel_dir=/config/pyronix_wheels

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "==> building $wheel"
mkdir -p "$work/src"
cp -R src/pyronix_cloud "$work/src/"
cp -R pyproject.toml README.md COPYING LICENSES "$work/"
find "$work" -name __pycache__ -prune -exec rm -rf {} +
sed -i.bak "s/^version = \"${base_version}\"/version = \"${version}\"/" \
    "$work/pyproject.toml"
uv build --quiet --wheel --directory "$work" -o "$work/dist"
[[ -f "$work/dist/$wheel" ]] || { echo "wheel not built: $wheel" >&2; exit 1; }

echo "==> copying to $host"
remote "mkdir -p $wheel_dir && rm -f $wheel_dir/pyronix_cloud-*.whl"
scp -q "${ssh_opts[@]}" "$work/dist/$wheel" "$host:$wheel_dir/"

# Unpack beside the live copy, then swap, so deleted modules do not linger.
requirement="pyronix-cloud @ file://$wheel_dir/$wheel"
COPYFILE_DISABLE=1 tar -C custom_components --exclude=__pycache__ -cf - pyronix_cloud |
    remote "set -e
        cd /config/custom_components
        rm -rf .pyronix_cloud.new && mkdir .pyronix_cloud.new
        tar -xf - -C .pyronix_cloud.new
        sed -i 's#\"pyronix-cloud==[^\"]*\"#\"$requirement\"#' \
            .pyronix_cloud.new/pyronix_cloud/manifest.json
        grep -q '$wheel' .pyronix_cloud.new/pyronix_cloud/manifest.json
        rm -rf pyronix_cloud && mv .pyronix_cloud.new/pyronix_cloud pyronix_cloud
        rmdir .pyronix_cloud.new"
echo "    integration + wheel $version installed"

if [[ "$restart" -eq 0 ]]; then
    echo "==> skipped restart (--no-restart); restart HA to load it"
    exit 0
fi

echo "==> checking config and restarting Home Assistant"
remote "ha core check >/dev/null && ha core restart >/dev/null"

echo "==> waiting for the UI"
for _ in $(seq 1 60); do
    code="$(remote "curl -s -o /dev/null -w '%{http_code}' http://172.30.32.1:8123/" ||
        true)"
    [[ "$code" == "200" ]] && break
    sleep 5
done
[[ "${code:-}" == "200" ]] || { echo "HA did not come back (HTTP ${code:-none})" >&2; exit 1; }

# Give a configured entry a moment to install the wheel and set up, then show
# pyronix log lines. Lines that could carry credentials are dropped, not shown.
sleep 15
echo "==> recent pyronix log lines"
remote "ha core logs 2>/dev/null | grep -i pyronix |
    grep -v -i -E 'pwd|password|usercode|user_code|<z>' | tail -20" || true
echo "==> done ($version)"
