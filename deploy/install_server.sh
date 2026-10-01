#!/usr/bin/env bash
# One-time provisioning on a supported Ubuntu host. Review the guide before running.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run using sudo.' >&2; exit 1; }
domain=${1:?Usage: sudo bash deploy/install_server.sh your.domain}
[[ "$domain" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]] || { echo 'Supply a DNS hostname, without https:// or a path.' >&2; exit 1; }
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
apt-get update
apt-get install -y python3 python3-venv git nginx curl util-linux
python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "This release setup targets Ubuntu 24.04 / Python 3.12"'
if ! id niassist >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/niassist --shell /usr/sbin/nologin niassist
fi
install -d -o root -g root -m 755 /opt/niassist /opt/niassist/releases
install -d -o niassist -g niassist -m 700 /var/lib/niassist /var/backups/niassist
install -d -o root -g niassist -m 750 /etc/niassist
if [[ ! -e /etc/niassist/niassist.env ]]; then
    sed "s/YOUR_DOMAIN/$domain/" "$source_dir/deploy/production.env.example" > /etc/niassist/niassist.env
    chown root:niassist /etc/niassist/niassist.env
    chmod 640 /etc/niassist/niassist.env
fi
# Never replace the session key or database: migrate existing instance data first.
install -m 644 "$source_dir"/deploy/systemd/niassist-* /etc/systemd/system/
install -m 755 "$source_dir/deploy/release.sh" /usr/local/sbin/niassist-release
install -m 644 "$source_dir/deploy/nginx/niassist-proxy.conf" /etc/nginx/snippets/niassist-proxy.conf
if [[ ! -e /etc/nginx/sites-available/niassist ]]; then
    sed "s/DOMAIN_NAME/$domain/g" "$source_dir/deploy/nginx/niassist.conf.template" > /etc/nginx/sites-available/niassist
fi
systemctl daemon-reload
printf '%s\n' 'Provisioned. Next: migrate data/key, edit the private environment file, deploy a release, then enable the Nginx site and HTTPS using the guide.'
