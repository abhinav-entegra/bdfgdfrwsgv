# database-container

Shared **PostgreSQL** for Nexus admin, client, and signaler. This repo holds the **container definition** and connection notes—not the data itself (data lives in the volume / Railway Postgres disk).

## Railway (recommended)

**Easiest:** In Railway click **New → Database → PostgreSQL**. You do **not** need this Git repo for that—Railway runs managed Postgres and gives you `DATABASE_URL`.

**If you deploy *this* repo as a service:** Railway needs a **Dockerfile** (included). The old error *“Error creating build plan with Railpack”* happened because there was only `docker-compose.yml`; Railpack does not build Compose files as one web service. After pulling the latest commit, redeploy; `railway.toml` forces a **Dockerfile** build.

1. Service **Variables** (required on first boot): `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (and optionally `PGDATA`).
2. Attach a **Volume** in Settings → mount at `/var/lib/postgresql/data` so data survives restarts.
3. Use **Networking** to get a TCP URL or private URL; build your apps’ `DATABASE_URL` from that (or use Railway’s variable reference if you linked services).

Alternatively: add **PostgreSQL** from the template (no Dockerfile repo).
2. Open the Postgres service → **Variables** (or **Connect**).
3. Copy **`DATABASE_URL`** (Railway provides the full URL, often starting with `postgresql://` or `postgres://`).
4. Paste the **same** `DATABASE_URL` into **every** app service:
   - **deploy-admin** (admin site)
   - **deploy-client** (client site)
   - **deploy-signaler** (optional)

Also use the **same** `CHAT_DB_ENCRYPTION_KEY` on all apps if your schema uses encrypted columns (Fernet). That value is a **secret**, not a “public key.”

### SSL

If your host gives an **SSL root certificate** for Postgres, append query params to the URL or set `PGSSLROOTCERT` as your platform documents. Most Railway internal URLs work without extra files.

## Local development (Docker)

From this folder:

```bash
cp .env.example .env
# edit .env — set POSTGRES_PASSWORD etc.
docker compose up -d
```

Use in your apps:

```env
DATABASE_URL=postgresql://nexus:nexus@localhost:5432/nexus
```

(Adjust user/password/db to match `.env`.)

## Migrations

Schema is created by the Flask apps on startup (`create_all` + bootstrap). For production changes, coordinate migrations from the **application** repos; this repo only defines **where** Postgres runs and how to connect.

## FAQ

**Is this SQL?** Yes. **PostgreSQL** is a relational database; you use **SQL** for queries. Your Nexus apps talk to it through SQLAlchemy, which generates SQL.

**How much data can it hold?** There is no fixed “app limit” in the code. Capacity depends on **disk** (Docker volume size, Railway plan / volume size, or your server disk). Postgres supports **very large** databases (terabytes on proper hardware). For Railway, check your **plan and volume** limits in their docs; upgrade storage if you grow out of it.
