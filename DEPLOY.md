# Deploying unipay to pay.cavendishza.org

Target: a Linux server that already runs UniTime on Tomcat. This puts unipay
behind nginx as a reverse proxy, alongside Tomcat, using CyberSource's
**test/sandbox** credentials (same ones already in the local `.env` — do not
switch `CYBS_ENVIRONMENT` to `production` until this has been tested end to
end on the real domain).

Replace `<SERVER_IP>` below with your actual value.

## 1. Move Tomcat off port 80, install nginx

On this box, Tomcat (`tomcat10`, Ubuntu package — `CATALINA_BASE=/var/lib/tomcat10`)
was bound directly to `0.0.0.0:80` with nothing in front of it. UniTime itself
is deployed at `/UniTime` (`/var/lib/tomcat10/webapps/UniTime` — case
matters, Tomcat context paths are case-sensitive), not at
`ROOT` — hitting the bare `timetable.cavendishza.org` domain previously
resolved straight to UniTime, but exactly how that redirect/mapping was done
wasn't found (no `/etc/tomcat10/Catalina/localhost/ROOT.xml` override was
present) — so nginx now does that redirect explicitly instead (see the
`timetable.cavendishza.org` vhost below) rather than depending on a Tomcat-side
config we couldn't locate.

```bash
sudo cp /etc/tomcat10/server.xml /etc/tomcat10/server.xml.bak
sudo ss -tlnp | grep ':8080'                      # confirm 8080 is free first
sudo sed -i 's/port="80"/port="8080"/' /etc/tomcat10/server.xml
sudo systemctl restart tomcat10
curl -I http://localhost:8080/UniTime/            # should respond directly

sudo apt update && sudo apt install -y nginx
```

## 2. DNS

Add an A record: `pay.cavendishza.org` → `<SERVER_IP>`. Confirm it resolves
before requesting a cert:

```bash
dig +short pay.cavendishza.org
```

## 3. Get the code onto the server

```bash
# from your local machine
scp -r "c:\Users\Admin\Desktop\Cavendish-Work\CyberSource\unipay" ubuntu@<SERVER_IP>:~/unipay
scp "c:\Users\Admin\Desktop\Cavendish-Work\CyberSource\unipay\.env" ubuntu@<SERVER_IP>:~/unipay/.env
```

(Or push to a private git repo and `git clone` on the server instead — easier
to redeploy later. Never commit `.env` either way.)

## 4. Python environment on the server

```bash
ssh ubuntu@<SERVER_IP>
cd ~/unipay
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt gunicorn
```

`django-extensions`/`Werkzeug`/`pyOpenSSL`/`cryptography` in requirements.txt
were only for the local self-signed-cert workaround — you can leave them
installed (harmless, `django_extensions` only loads when `DEBUG=True`) or
skip them here since nginx+certbot gives you a real cert.

## 5. Server-side `.env` — only 3 lines change from local

Keep every `CYBS_*` line exactly as it is locally (same sandbox
Zanaco/Absa credentials, `CYBS_ENVIRONMENT=test`). Only change:

```
DJANGO_SECRET_KEY=<generate a new one — see below, don't reuse the local one>
DJANGO_DEBUG=False
ALLOWED_HOSTS=pay.cavendishza.org
```

Generate a real secret key:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

Then:
```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check_cybersource     # should show PASS for both accounts
```

## 6. Gunicorn as a systemd service

`/etc/systemd/system/unipay.service`:
```ini
[Unit]
Description=unipay gunicorn
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/unipay
ExecStart=/home/ubuntu/unipay/.venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8001 --workers 3
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now unipay
sudo systemctl status unipay
```

Binding to `127.0.0.1` (not `0.0.0.0`) matters: it's what makes
`SECURE_PROXY_SSL_HEADER` in `config/settings.py` safe — only nginx can reach
gunicorn directly, so nothing external can spoof the `X-Forwarded-Proto`
header Django trusts to know a request came in over real HTTPS.

## 7. Nginx vhosts

`/etc/nginx/sites-available/timetable.cavendishza.org` — restores the old
"bare domain resolves to UniTime" behavior via an explicit redirect, then
proxies everything else to Tomcat on its new port:
```nginx
server {
    listen 80;
    server_name timetable.cavendishza.org;

    location = / {
        # 302 (not 301) + explicit no-cache: a plain 301 is cacheable by
        # browsers indefinitely by default, which can leave clients stuck
        # on a stale redirect target forever if this path ever changes.
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        return 302 /UniTime/;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

`/etc/nginx/sites-available/pay.cavendishza.org` — also redirects the bare
domain to `/pay/`, same reasoning as above (so `pay.cavendishza.org` alone
lands on the payment form instead of a Django 404 for the un-routed root path):
```nginx
server {
    listen 80;
    server_name pay.cavendishza.org;

    location = / {
        add_header Cache-Control "no-cache, no-store, must-revalidate" always;
        return 302 /pay/;
    }

    location /static/ {
        alias /home/ubuntu/unipay/staticfiles/;
    }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/timetable.cavendishza.org /etc/nginx/sites-enabled/
sudo ln -s /etc/nginx/sites-available/pay.cavendishza.org /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

## 8. Real TLS cert

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d pay.cavendishza.org
```

Certbot rewrites this server block to add a `listen 443 ssl` section. **Open
the file afterward and confirm the `X-Forwarded-Proto` line and the
`location = /` redirect block are still present in the new 443 block** —
certbot usually preserves existing directives, but it's worth checking
before testing: the former is what makes CyberSource's capture-context call
see `https://` instead of `http://`, and the latter is what makes the bare
`https://pay.cavendishza.org/` land on the payment form instead of a 404.

## 9. Firewall

```bash
sudo ufw allow 'Nginx Full'
sudo ufw status
```

## 10. Test

Visit `https://pay.cavendishza.org/pay/`, submit the form, confirm checkout
loads CyberSource's card entry UI with no error banner, and complete a
sandbox test-card payment through to the receipt page.

## Redeploying after a code change

```bash
ssh ubuntu@<SERVER_IP>
cd ~/unipay && git pull   # or scp the changed files over
source .venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart unipay
```
