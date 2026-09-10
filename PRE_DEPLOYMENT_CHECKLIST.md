# Pre-Deployment Checklist

## Phase 1: Security Hardening ✓

### Environment Variables
- [ ] Generated new SECRET_KEY: `@ei9+r-v18zbkm@$!*le8j!l2w-k2)q&$=q4h3k&^=fbm%3wgx`
  - Copy this to your `.env.production` file
- [ ] Set `DEBUG=False` ✓ (Already done)
- [ ] Created `.env.production.example` with all required variables ✓
- [ ] Flutterwave keys fixed (placeholder set) ✓
- [ ] All API keys ready for production (replace placeholders)

### Settings Updates
- [ ] Added SECURE_HSTS_SECONDS ✓
- [ ] Added SECURE_HSTS_INCLUDE_SUBDOMAINS ✓
- [ ] Added SECURE_HSTS_PRELOAD ✓
- [ ] Added SECURE_BROWSER_XSS_FILTER ✓
- [ ] Added SECURE_CONTENT_SECURITY_POLICY ✓

### Code Security
- [ ] No hardcoded secrets in code
- [ ] All API keys in environment variables
- [ ] `.env` file in .gitignore (not committed)
- [ ] `.env.production.example` committed (no secrets)

---

## Phase 2: Database Setup ⚠️

Choose your deployment platform first, then configure database:

### PostgreSQL Setup (Recommended)
```bash
# 1. Install PostgreSQL locally
# 2. Create database:
createdb hoxobil_db

# 3. Run migrations:
python manage.py migrate

# 4. Create superuser (for admin):
python manage.py createsuperuser

# 5. For production, get DATABASE_URL from your platform:
# Render: Provided automatically
# Heroku: heroku addons:create heroku-postgresql:standard-0
# DigitalOcean: Configure in control panel
```

### Verify Database
```bash
# Test the connection
python manage.py dbshell
```

---

## Phase 3: Static Files & Media ⚠️

```bash
# Run collectstatic to prepare static files
python manage.py collectstatic --noinput

# Verify staticfiles/ folder contains:
# - admin/
# - images/
# - videos/
# - css/
# - js/
```

**Note**: For production, consider using CDN:
- Cloudflare (free)
- AWS S3
- DigitalOcean Spaces
- Backblaze B2

---

## Phase 4: Dependencies ✓

- [ ] Updated `requirements.txt` for Python 3.14 compatibility ✓
- [ ] All packages installed: `pip install -r requirements.txt` ✓
- [ ] Added deployment server: `gunicorn` ✓

### Verify Installation
```bash
pip list | grep -E "Django|gunicorn|psycopg2|dj-database-url"
```

---

## Phase 5: Testing ⚠️

```bash
# 1. Run Django checks
python manage.py check --deploy

# 2. Run tests
python manage.py test

# 3. Test with DEBUG=False locally
DEBUG=False python manage.py runserver

# 4. Test critical user flows:
#    - Homepage loads
#    - Product browsing works
#    - Cart functionality
#    - Payment page displays
#    - Admin panel accessible at /admin/
```

**Expected Output**:
```
✓ All migrations applied
✓ Admin panel accessible
✓ Static files served
✓ Database connection OK
```

---

## Phase 6: Deployment Platform Setup ⚠️

### Option A: Render.com (Recommended for beginners)

1. **Create Account**: https://render.com
2. **Connect GitHub**: Authorize your repo
3. **Create Web Service**:
   - Name: `hoxobil-store`
   - Runtime: `Python 3.12`
   - Build Command: (use render.yaml)
   - Start Command: (use render.yaml)
4. **Create Database**:
   - PostgreSQL 15
   - Note the DATABASE_URL
5. **Set Environment Variables**:
   ```
   DEBUG=False
   SECRET_KEY=@ei9+r-v18zbkm@$!*le8j!l2w-k2)q&$=q4h3k&^=fbm%3wgx
   DATABASE_URL=<from-postgres-db>
   RENDER_EXTERNAL_HOSTNAME=hoxobil-store.onrender.com
   CORS_ALLOWED_ORIGINS=https://hoxobil.store,https://www.hoxobil.store
   EMAIL_HOST=smtp.gmail.com
   EMAIL_PORT=587
   EMAIL_HOST_USER=<your-email>
   EMAIL_HOST_PASSWORD=<app-password>
   FLUTTERWAVE_PUBLIC_KEY=FLWPUBK-<production-key>
   FLUTTERWAVE_SECRET_KEY=FLWSECK-<production-key>
   PAYSTACK_PUBLIC_KEY=pk_live_<production-key>
   PAYSTACK_SECRET_KEY=sk_live_<production-key>
   PAYSTACK_MODE=live
   PRINTFUL_ACCESS_TOKEN=<token>
   PRINTFUL_STORE_ID=<store-id>
   ANTHROPIC_API_KEY=<api-key>
   SERPER_API_KEY=<api-key>
   PUBLIC_BASE_URL=https://hoxobil.store
   ```
6. **Deploy**: Click "Deploy" button

### Option B: Heroku (Simpler but paid)

```bash
# 1. Install Heroku CLI
# 2. Login
heroku login

# 3. Create app
heroku create hoxobil-store

# 4. Add PostgreSQL add-on
heroku addons:create heroku-postgresql:standard-0 -a hoxobil-store

# 5. Set environment variables
heroku config:set DEBUG=False -a hoxobil-store
heroku config:set SECRET_KEY=@ei9+r-v18zbkm@$!*le8j!l2w-k2)q&$=q4h3k&^=fbm%3wgx -a hoxobil-store
# ... set all other variables

# 6. Deploy
git push heroku main
```

### Option C: DigitalOcean App Platform

1. Go to DigitalOcean > App Platform
2. Create app
3. Connect GitHub repo
4. Configure:
   - Build command: `pip install -r requirements.txt && python manage.py migrate`
   - Run command: `gunicorn hoxobil_store.wsgi:application`
5. Add PostgreSQL database from panel
6. Set environment variables
7. Deploy

### Option D: Self-Hosted (DigitalOcean Droplet, AWS EC2, etc.)

See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for detailed setup.

---

## Phase 7: Domain Configuration ⚠️

### Point Domain to Deployment

1. **Get Deployment URL**: Your platform provides this (e.g., `hoxobil-store.onrender.com`)
2. **Update DNS**: Go to your domain registrar (GoDaddy, Namecheap, etc.)
3. **Add CNAME Record**:
   - Name: `www`
   - Value: `hoxobil-store.onrender.com`
4. **Add A Record** (if needed):
   - Name: `@`
   - Value: IP provided by platform
5. **Wait for DNS**: Can take 24 hours (usually 5 mins)

### Add Custom Domain in Platform

1. **Render**: Settings > Custom Domains
2. **Heroku**: Settings > Domains
3. **DigitalOcean**: Select your domain in app settings

---

## Phase 8: Email Configuration ⚠️

### Gmail Setup (Easiest)
1. Enable 2-Factor Authentication on Gmail
2. Create App Password: https://myaccount.google.com/apppasswords
3. Set in deployment:
   ```
   EMAIL_HOST=smtp.gmail.com
   EMAIL_HOST_USER=your-email@gmail.com
   EMAIL_HOST_PASSWORD=<16-char-app-password>
   EMAIL_PORT=587
   EMAIL_USE_TLS=True
   ```

### Alternative: SendGrid
1. Create account: https://sendgrid.com
2. Verify sender email
3. Get API key
4. Set in deployment:
   ```
   EMAIL_HOST=smtp.sendgrid.net
   EMAIL_HOST_USER=apikey
   EMAIL_HOST_PASSWORD=<SendGrid-API-Key>
   EMAIL_PORT=587
   ```

---

## Phase 9: Payment Gateway Testing ⚠️

### Flutterwave
1. Go to Flutterwave Dashboard
2. Switch to LIVE mode
3. Get production keys
4. Test payment with real card (or test card if available)
5. Verify webhook configuration:
   - Webhook URL: `https://yourdomain.com/webhook/flutterwave/`
   - Events: Payment complete, payment failed

### Paystack
1. Go to Paystack Dashboard
2. Switch to LIVE mode
3. Get live keys (NOT test keys)
4. Set `PAYSTACK_MODE=live`
5. Test payment with real card
6. Verify webhook configuration:
   - Webhook URL: `https://yourdomain.com/webhook/paystack/`
   - Select all events

---

## Phase 10: SSL/HTTPS ✓

- [ ] SSL Certificate: Automatic with Render/Heroku/DigitalOcean
- [ ] HTTPS redirect: Enabled in Django settings ✓
- [ ] Test HTTPS: Visit https://yourdomain.com
- [ ] Check SSL: https://www.sslshopper.com/ssl-checker.html

---

## Phase 11: Monitoring & Alerts ⚠️

### Error Tracking (Recommended: Sentry)
```bash
# 1. Create account at sentry.io
# 2. Create project for Django
# 3. Install package
pip install sentry-sdk

# 4. Add to settings.py
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

if not DEBUG:
    sentry_sdk.init(
        dsn="<your-sentry-dsn>",
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
    )
```

### Uptime Monitoring
- UptimeRobot (free): https://uptimerobot.com
- Pingdom: https://www.pingdom.com
- StatusCake: https://www.statuscake.com

### Performance Monitoring
- Your platform's built-in monitoring
- New Relic (free tier)
- DataDog

---

## Phase 12: Final Pre-Production Testing ⚠️

```bash
# 1. Verify all checks pass
python manage.py check --deploy

# 2. Test in production environment
# Visit your deployment URL and test:
- [ ] Homepage loads
- [ ] Product pages display
- [ ] Add to cart works
- [ ] Checkout page visible
- [ ] Payment buttons appear
- [ ] Admin panel accessible
- [ ] Email notifications sent
- [ ] Static files load (images, CSS, JS)
```

---

## Phase 13: Launch! 🚀

1. **Final Backup**: Backup production database before going live
2. **Announce**: Tell users the site is live
3. **Monitor**: Watch logs for errors
4. **Support**: Be ready to handle issues

---

## Post-Launch Tasks

- [ ] Monitor error logs daily for first week
- [ ] Set up automated backups
- [ ] Document any deployment-specific procedures
- [ ] Create incident response plan
- [ ] Schedule regular security updates
- [ ] Monitor payment processing
- [ ] Test email delivery

---

## Troubleshooting Commands

```bash
# Check if deployment is working
curl -I https://yourdomain.com

# View deployment logs
# Render: Logs tab in dashboard
# Heroku: heroku logs --tail -a hoxobil-store

# Restart application
# Render: Manual deploy button
# Heroku: heroku restart -a hoxobil-store

# SSH into server (if available)
# Render: Not available via SSH
# DigitalOcean: ssh root@<droplet-ip>

# Check current environment variables
# Render: Environment tab in dashboard
# Heroku: heroku config -a hoxobil-store

# Run management command in production
# Render: Run migrations from dashboard
# Heroku: heroku run python manage.py createsuperuser -a hoxobil-store
```

---

## Emergency Rollback

If production fails:

1. **Identify the issue**: Check logs
2. **Rollback**: Redeploy previous working version
   - Render: Select previous deploy
   - Heroku: `heroku releases -a hoxobil-store` then `heroku releases:rollback -a hoxobil-store`
3. **Investigate**: Debug locally
4. **Fix & test**: Verify before redeployment

---

## Files Created/Modified

✓ `.env` - Updated with DEBUG=False, fixed Flutterwave keys
✓ `.env.production.example` - Template for production environment
✓ `hoxobil_store/settings.py` - Added HSTS and security headers
✓ `requirements.txt` - Updated for Python 3.14 compatibility
✓ `render.yaml` - Render.com deployment configuration
✓ `Procfile` - Heroku deployment configuration
✓ `DEPLOYMENT_GUIDE.md` - Comprehensive deployment guide
✓ `DEPLOYMENT_CHECKLIST.md` - Initial checklist
✓ `PRE_DEPLOYMENT_CHECKLIST.md` - This file

---

## Next Steps

1. **Choose deployment platform**: Render (recommended), Heroku, or DigitalOcean
2. **Prepare environment variables**: Copy template, fill in production values
3. **Test locally**: Run `DEBUG=False python manage.py runserver`
4. **Set up database**: PostgreSQL in your chosen platform
5. **Configure domain**: Point DNS to deployment URL
6. **Set up email**: Gmail or SendGrid
7. **Configure payment gateways**: Flutterwave and Paystack
8. **Deploy**: Push to your platform
9. **Verify**: Test all critical flows
10. **Launch**: Go live!

---

**Questions?** See DEPLOYMENT_GUIDE.md for detailed instructions.
