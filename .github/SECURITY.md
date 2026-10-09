# Security

*Report a vulnerability privately through GitHub (Security › Report a
vulnerability), never in a public issue.*

## What the studio exposes

- **The local server** (`gamestudio serve`, started by the application) listens
  on `127.0.0.1`. Every `/api/` route requires a token, drawn on first run and
  stored in `data/run/api-token` (readable by its owner only); only the static
  front end, which triggers nothing, is served without it. The server answers
  only a loopback `Host` header, against DNS rebinding. Both locks matter: the
  terminal routes start programs.
- `GAMESTUDIO_API_HOST` or `--host` change the listening address. Do not expose
  the server beyond the machine: it is not built for that.
- Deleting `data/run/api-token` renews the token at the next start.

## Keys

`RUNWARE_API_KEY` and `TRIPO_API_KEY` live in the `.env` at the studio root,
ignored by git — only `.env.example` is tracked. They are never copied into a
project folder. A key committed by mistake must be revoked at the provider:
removing it from the history is not enough.

Every paid operation requires explicit confirmation. An agent started in the
Chats window runs with your rights on the machine: it can read this `.env` like
the rest of your files. Only run agents there that you would trust with the key.

## Reporting a vulnerability

Through GitHub's private reporting: the repository's **Security › Report a
vulnerability** tab. No public issue. Describe the version (commit), what an
attacker gains, and how to reproduce it.
