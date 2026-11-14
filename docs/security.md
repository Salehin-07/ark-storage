# Security notes

* Single-account login; passwords are hashed (scrypt, via Werkzeug). Failed logins are rate limited (6 per 5 minutes per IP).
  Session cookies are HttpOnly + SameSite=Lax; every state-changing request needs a CSRF token header.
* Files that browsers could execute (`.html`, `.svg`, `.js`, `.xml`) are served with `Content-Security-Policy: sandbox`.
* Share links are signed, expiring and read-only; they stop working when the file is removed. Rotating `ARK_SECRET_KEY` revokes all of them.
* The GitHub token and passphrase are write-only in the UI and stored in `data/secrets.json` (mode 0600) when entered there;
  prefer environment variables. Tokens are scrubbed from error messages.
* Use HTTPS in front of the server (Render, Caddy, nginx, Cloudflare). The app itself speaks plain HTTP.
* Use a **fine-grained token limited to one repository** with only "Contents: read and write".
* Encryption protects contents; file names, folder structure, sizes (approximately) and modification times stay visible in the repo.
