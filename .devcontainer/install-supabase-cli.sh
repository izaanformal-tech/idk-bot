#!/bin/sh
set -eu

arch="$(uname -m)"
case "$arch" in
    x86_64) asset="supabase_linux_amd64.tar.gz" ;;
    aarch64|arm64) asset="supabase_linux_arm64.tar.gz" ;;
    *) echo "Unsupported Supabase CLI architecture: $arch" >&2; exit 1 ;;
esac

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

url="$(curl -fsSL https://api.github.com/repos/supabase/cli/releases/latest \
    | sed -n 's/.*"browser_download_url": "\([^"]*'"$asset"'\)".*/\1/p' \
    | head -n 1)"

if [ -z "$url" ]; then
    echo "Could not find a Supabase CLI release for $arch" >&2
    exit 1
fi

curl -fsSL "$url" -o "$tmpdir/supabase.tar.gz"
tar -xzf "$tmpdir/supabase.tar.gz" -C "$tmpdir"
if [ -w /usr/local/bin ]; then
    install -m 0755 "$tmpdir/supabase" /usr/local/bin/supabase
else
    sudo install -m 0755 "$tmpdir/supabase" /usr/local/bin/supabase
fi
supabase --version
