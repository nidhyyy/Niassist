# Niassist release and EC2 migration guide

This package prepares deployment; it has NOT changed your GitHub repository,
EC2 instance, DNS, certificates or secrets. The scripts target **Ubuntu 24.04
with Python 3.12**, Nginx and one EC2 host. Check your actual OS before running.
If the current server differs, adapt the setup first rather than replacing it.

## 1. Keep the working Windows copy safe

Stop `app.py` and `worker.py`. Back up the entire existing project, especially
`.env` and `instance/`. Copy this release's source files over that SAME working
folder, preserving `.env`, `.venv313`, and `instance/`.

New production files include settings.py, deploy/, scripts/, tests/test_release.py,
requirements-linux.lock, updated app.py/worker.py/workflow_store.py, and the two
GitHub workflows. The Windows app still uses its existing instance directory.

Local verification:

```bat
.\.venv313\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv313\Scripts\python.exe -m pytest -q
npm ci
npm test
```

Start the app and worker in separate terminals as before. Verify the real-user
workflow with a small PDF or TXT file containing information you can verify: registration/login, follow-up
chat, upload, cited answer, reviewed task, and a reminder two minutes ahead.
Choose your own future reminder time for testing.
`/health` is liveness; `/ready` reports DB availability, worker heartbeat and
presence of an AI key without spending tokens or verifying Gemini credentials.

## 2. Check the existing EC2 setup before making changes

In your EC2 SSH terminal (never share private keys, .env contents or passwords):

```bash
lsb_release -ds
python3 --version
sudo nginx -t
sudo ss -ltnp '( sport = :8000 )'
systemctl list-units --type=service --all 'niassist*' 'gunicorn*'
```

Determine the current app path, Nginx server block and process supervisor. Your
old workflow used `nohup gunicorn`; identify its exact master process and how it
starts. Stop that specific application during cutover so port 8000 is free.
Do not kill every Gunicorn process on a host running other applications.
Back up the old Nginx site config. Do not enable two server blocks for one domain.

The deployment scripts do not automatically shut down your legacy installation
or replace unrelated Nginx sites. This needs the actual server inventory first.

## 3. Prepare a domain and HTTPS

Choose a domain/subdomain you control, point its A record to the EC2 public IP
(prefer a stable assigned address), and allow inbound TCP 80 and 443. Keep port
8000 private: Gunicorn will bind 127.0.0.1. Keep SSH restricted to authorized
sources. GitHub-hosted runners need an SSH access path; if your firewall only
allows your laptop, use a trusted runner/access arrangement or deploy manually.

The app trusts exactly one reverse proxy when NIASSIST_PROXY_HOPS=1. The supplied
Nginx snippet overwrites forwarded IP/protocol headers. Do not expose Gunicorn
directly or enable this setting behind an unknown proxy chain.

## 4. Commit the reviewed code, but keep auto-deploy off

The workflow's deployment job is disabled unless the GitHub repository variable
DEPLOY_ENABLED is the literal string `true`. Push the updated files to your
repository with that variable unset. Ensure `.env`, instance/, SQLite files,
virtual environments and node_modules are ignored and not staged.

Run the Tests workflow and wait for success. Get the full commit SHA from
GitHub or `git rev-parse HEAD`. Do not use the old repo commit: it lacks this
release's service/configuration files. No push was performed by this work.

## 5. One-time server preparation

After checking the OS and current services, check out the reviewed commit in a
staging folder on EC2. From that folder:

```bash
sudo bash deploy/install_server.sh YOUR_DOMAIN
```

This installs OS dependencies, creates a restricted service user, and installs
systemd files, the release helper and the Nginx snippet. It does not overwrite
an existing /etc/niassist/niassist.env, Nginx Niassist site, database or key. It does
not enable the Nginx site or acquire a certificate yet. It uses these paths:

| Purpose | Path |
| --- | --- |
| Immutable code releases | /opt/niassist/releases/FULL_SHA |
| Active release symlink | /opt/niassist/current |
| Persistent data and key | /var/lib/niassist |
| Private configuration | /etc/niassist/niassist.env |
| Local snapshots | /var/backups/niassist |

System services have 1 GiB memory limits each. These are ceilings, not a claim
that your EC2 instance has enough RAM. Check available memory and adjust worker
counts/limits based on the instance before accepting uploads.

## 6. Migrate data without overwriting another database

Choose ONE authoritative database: your current local working database or the
existing server database. There is no automatic merge of two databases.
Stop the processes writing to the chosen source before copying it. Transfer
`instance/chats.sqlite3` and `instance/session-secret` through your authorized SSH
connection, then install them at /var/lib/niassist with owner niassist:niassist
and mode 600. Back up any existing destination first; never blindly overwrite it.
Do not copy a live database file while the app/worker writes to it. The provided
SQLite backup script can create a consistent snapshot instead.

Edit the private environment file on the server:

```bash
sudoedit /etc/niassist/niassist.env
```

Set the real domain in NIASSIST_TRUSTED_HOSTS, the Gemini key and model, and keep
COOKIE_SECURE=1. If your old app used SECRET_KEY in .env rather than the generated
session-secret file, carry that exact value into this private environment file.
Never paste it into chat, workflow YAML or source control.

For a genuinely NEW installation with no prior key to preserve, generate one
without printing it:

```bash
sudo -u niassist python3 -c 'from pathlib import Path; import secrets; p=Path("/var/lib/niassist/session-secret"); f=p.open("x"); f.write(secrets.token_hex(32)); f.close(); p.chmod(0o600)'
```

The `x` mode refuses to overwrite an existing key. After moving from localhost
to your domain, sign in again: browser cookies do not transfer between hosts.
Accounts/chats remain available if you migrated the correct database.

## 7. First release and cutover

Stop the previously identified legacy web/worker processes during the maintenance
window, then deploy the tested FULL_SHA:

```bash
sudo /usr/local/sbin/niassist-release FULL_SHA
```

The helper fetches that commit from the fixed public Niassist repo, builds a
release environment as the non-root service user, installs the Linux lock file,
checks dependencies, and makes release files read-only to the service user.
It then stops the managed services, snapshots existing data, switches the current
symlink, starts web/worker and checks `/ready` for the expected commit.
It enables both services at boot and the daily backup timer only after readiness.

A failed update restores the previous code symlink if available and restarts the
old services. It never silently restores an older database. This currently works
with additive migrations; schema-breaking releases need a reviewed migration and
rollback plan. An initial failed release has no previous code to restore: fix
the logged configuration problem and restart the services, then check readiness.
An existing release directory is not silently reused or overwritten.

Inspect:
```bash
sudo systemctl status niassist-web niassist-worker --no-pager
sudo journalctl -u niassist-web -u niassist-worker -n 80 --no-pager
curl --fail http://127.0.0.1:8000/ready
```

Enable the intended Nginx site after disabling ONLY the conflicting legacy site
identified earlier. Leave unrelated sites intact:

```bash
sudo ln -s /etc/nginx/sites-available/niassist /etc/nginx/sites-enabled/niassist
sudo nginx -t
sudo systemctl reload nginx
```

If the link already exists, do not recreate it. Install Certbot using the official
Ubuntu/Nginx instructions, then request a certificate for your real domain:

```bash
sudo certbot --nginx -d YOUR_DOMAIN --redirect
sudo certbot renew --dry-run
```

Certbot needs correct DNS and HTTP reachability. The domain's HTTP page may load
before issuance, but secure-cookie login is intended for HTTPS only. Do not
collect user credentials over HTTP. Certbot edits the Nginx server block; later
release updates intentionally leave that TLS configuration intact.

The supplied Nginx config allows 6 MB request bodies, disables response buffering
for streamed answers and gives AI requests a longer upstream timeout. `/ready`
is blocked at Nginx; `/health` remains public. Per-IP edge request limits protect
login and AI endpoints, and all model requests share the account's six/minute
application limit. Successful readiness does NOT verify live Gemini access.

## 8. Validate the deployed workflow and capture screenshots

Visit https://YOUR_DOMAIN and repeat the full demo from step 1. Check streaming,
source citations, saved chats after logout/login, and in-app reminder delivery
with the browser closed. Restart the worker and verify overdue pending reminders
arrive once. Inspect logs for errors; logs should not contain prompts/passwords.

Capture current screenshots using fictional demo content only:
- Sign-in page with empty password field.
- Chat with a follow-up response.
- Document answer with a visible source quote/page.
- Editable task review before confirmation.
- Task list and delivered reminder inbox.

Save them in screenshots/release/ and link them in the README once captured.
No new live screenshots or real-Gemini end-to-end test were produced here; browser
installation failed in the build workspace, and no EC2 access was provided.

## 9. Enable tested CI/CD after the first successful manual release

Create GitHub secrets (repository or the production environment):
EC2_HOST, EC2_USER, EC2_SSH_KEY, and EC2_KNOWN_HOSTS. The SSH user must be authorized
to run the root-owned /usr/local/sbin/niassist-release without an interactive sudo
prompt; provision that narrowly according to your server policy. Do not grant
new broad sudo access as a shortcut. The repo is public; the server fetches source
without a GitHub token. Protect main and review who can trigger production.

EC2_KNOWN_HOSTS must contain the verified public host key entry for that SSH
hostname. Obtain/verify its fingerprint through your trusted EC2 console or an
already trusted connection. Do not bypass host-key checking or blindly trust a
key fetched over the same unverified network path.

Set DEPLOY_ENABLED=true only after the manual deployment and HTTPS checks succeed.
Pushes to main will run Python/UI tests and shell validation, then deploy that
exact tested SHA through SSH. Deployments are serialized; a new push does not
cancel an in-progress deployment. Tests failing prevents the deploy job.
Changing systemd/Nginx/helper files in later source commits does not update the
installed system files automatically; apply those reviewed infrastructure changes
separately and run daemon-reload/nginx -t as appropriate.

## 10. Backups, recovery and daily operation

```bash
sudo systemctl list-timers niassist-backup.timer
sudo systemctl start niassist-backup.service
sudo journalctl -u niassist-backup -n 30 --no-pager
```

The timer runs around 02:00 UTC daily. It copies SQLite using its backup API and
checks snapshot integrity, then copies session-secret if present. Snapshots are
private to the service account. Back up /etc/niassist/niassist.env separately,
especially when SECRET_KEY is stored there. No automatic deletion/retention is
configured: monitor disk usage and copy backups to a protected off-host location
using your chosen AWS backup/storage arrangement. Local snapshots alone do not
protect against instance/disk loss.

For a code-only manual rollback: stop web/worker, point /opt/niassist/current at
a verified prior release, restart, and check /ready. Do not point it at arbitrary
paths. For database restore: stop both services, take another snapshot of current
data, verify the desired backup, restore the DB/key with correct ownership, then
restart. A restore discards data created after the snapshot, so review it before
performing it. There is deliberately no automatic destructive restore command.

The public feature limits remain documented in README.md: in-app reminders,
keyword retrieval, no OCR/email notifications/password reset. Parser subprocesses
run under the isolated worker service with no network and restricted filesystem
writes, but a public high-risk upload service should use a dedicated parser
container with stricter resource isolation rather than treating this as a full
sandbox.

## Primary references

- https://flask.palletsprojects.com/en/stable/deploying/proxy_fix/
- https://nginx.org/en/docs/http/ngx_http_proxy_module.html
- https://certbot.eff.org/instructions?os=snap&ws=nginx
- https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup

## Validation performed for this package

- 75 Python tests passed, including persistent-path configuration, production
  fail-closed settings, proxy behavior, readiness and consistent backup contents.
- 10 frontend regression tests passed (including voice error handling).
- Shell scripts passed bash syntax checks; service files passed systemd syntax
  verification with local placeholder executable paths. Real service startup and
  Nginx configuration must still be verified on EC2.
- Python 3.12.14 on Linux was used; dependency consistency check passed.
- No GitHub push, EC2 command, DNS/certificate operation or deployment was run.
