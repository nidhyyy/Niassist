#!/usr/bin/env bash
# Fetch and install one tested commit from the fixed Niassist repository.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run using sudo.' >&2; exit 1; }
sha=${1:?Usage: sudo niassist-release FULL_COMMIT_SHA}
[[ "$sha" =~ ^[a-f0-9]{40}$ ]] || { echo 'Expected a full 40-character commit SHA.' >&2; exit 1; }
exec 9>/run/lock/niassist-release.lock
flock -n 9 || { echo 'Another release is running.' >&2; exit 1; }
root=/opt/niassist
release="$root/releases/$sha"
[[ -f /etc/niassist/niassist.env ]] || { echo 'Run install_server.sh first.' >&2; exit 1; }
[[ ! -e "$release" ]] || { echo 'Release directory exists. Inspect it and use the rollback guide or remove only a failed, unused release before retrying.' >&2; exit 1; }
install -d -o niassist -g niassist -m 755 "$release"
# Git runs unprivileged. No private SSH credential is required for this public repo.
runuser -u niassist -- git -C "$release" init -q
runuser -u niassist -- git -C "$release" fetch --depth 1 https://github.com/nidhyyy/Niassist.git "$sha"
runuser -u niassist -- git -C "$release" checkout --detach FETCH_HEAD
[[ "$(git -c safe.directory="$release" -C "$release" rev-parse HEAD)" == "$sha" ]]
runuser -u niassist -- python3 -m venv "$release/.venv"
runuser -u niassist -- "$release/.venv/bin/python" -m pip install --disable-pip-version-check -r "$release/requirements-linux.lock"
runuser -u niassist -- "$release/.venv/bin/python" -m pip check
# Freeze release contents before service processes execute them.
printf '%s\n' "$sha" > "$release/RELEASE"
chown -R root:root "$release"
chmod -R go-w "$release"
previous=$(readlink -f "$root/current" 2>/dev/null || true)
restore_services() {
    if [[ -n "$previous" ]]; then
        ln -s "$previous" "$root/recovery-$sha"
        mv -Tf "$root/recovery-$sha" "$root/current"
        systemctl restart niassist-web niassist-worker || true
    fi
}
rollback_on_error() {
    trap - ERR
    systemctl stop niassist-worker niassist-web || true
    restore_services
    echo 'Release operation failed; attempted to restore previous services. Database kept intact.' >&2
}
trap rollback_on_error ERR
# Short maintenance window. Stop app writers before migration snapshot.
systemctl stop niassist-worker niassist-web || true
if [[ -f /var/lib/niassist/chats.sqlite3 ]]; then
    if ! runuser -u niassist -- "$release/.venv/bin/python" "$release/scripts/backup.py" \
        --database /var/lib/niassist/chats.sqlite3 --destination /var/backups/niassist --secret /var/lib/niassist/session-secret; then
        restore_services
        echo 'Backup failed; previous release restored.' >&2
        exit 1
    fi
fi
# A heartbeat from the stopped worker must not make the new release look ready.
if [[ -f /var/lib/niassist/chats.sqlite3 ]]; then
    runuser -u niassist -- "$release/.venv/bin/python" -c 'import sqlite3; db=sqlite3.connect("/var/lib/niassist/chats.sqlite3"); exists=db.execute("SELECT 1 FROM sqlite_master WHERE name=?", ("worker_health",)).fetchone(); db.execute("DELETE FROM worker_health") if exists else None; db.commit(); db.close()'
fi
ln -s "$release" "$root/next-$sha"
mv -Tf "$root/next-$sha" "$root/current"
healthy=0
if systemctl start niassist-web niassist-worker; then
    for attempt in $(seq 1 45); do
        if systemctl is-active --quiet niassist-web niassist-worker &&
           curl --fail --silent --max-time 3 http://127.0.0.1:8000/ready |
           "$release/.venv/bin/python" -c 'import json,sys; r=json.load(sys.stdin); sys.exit(0 if r.get("release")==sys.argv[1] and r.get("status")=="ready" else 1)' "$sha"; then
            healthy=1; break
        fi
        sleep 2
    done
fi
if [[ $healthy -ne 1 ]]; then
    systemctl stop niassist-worker niassist-web || true
    restore_services
    echo 'Readiness failed; code rolled back if a previous release exists. The database was NOT restored automatically. Inspect service logs.' >&2
    exit 1
fi
if [[ -n "$previous" ]]; then
    ln -sfn "$previous" "$root/previous"
fi
trap - ERR
systemctl enable niassist-web niassist-worker niassist-backup.timer
systemctl start niassist-backup.timer
printf 'Release %s is ready. HTTPS and the external user workflow still need checking.\n' "$sha"
