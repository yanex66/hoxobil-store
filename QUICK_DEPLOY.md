# 🚀 Quick Deployment Reference Card

## Your Site is Ready! Here's the 5-Minute Setup

### Step 1: Choose Your Platform
- **Render** ⭐ (Free to start) - https://render.com
- **Heroku** ($7/mo) - https://heroku.com
- **DigitalOcean** ($5/mo) - https://digitalocean.com

### Step 2: Copy Environment Variables Template
```bash
cp .env.production.example .env.production
# DON'T COMMIT .env.production (has secrets)
```

### Step 3: Fill In Production Values

#### Production SECRET_KEY
```
@ei9+r-v18zbkm@$!*le8j!l2w-k2)q&$=q4h3k&^=fbm%3wgx
```

#### Database (from your platform)
```
DATABASE_URL=postgresql://user:pass@host/db
```

#### Email (Gmail example)
```
EMAIL_HOST=smtp.gmail.com
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=<16-char-app-password-from-Google>
```

#### Payment Keys (replace with production keys)
```
FLUTTERWAVE_PUBLIC_KEY=FLWPUBK-XXXXX
FLUTTERWAVE_SECRET_KEY=FLWSECK-XXXXX
PAYSTACK_PUBLIC_KEY=pk_live_XXXXX
PAYSTACK_SECRET_KEY=sk_live_XXXXX
PAYSTACK_MODE=live
```

#### Domain
```
RENDER_EXTERNAL_HOSTNAME=yourdomain.com
CORS_ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
PUBLIC_BASE_URL=https://yourdomain.com
```

### Step 4: Platform Setup

#### For Render.com
1. Sign up at https://render.com
2. Connect your GitHub repo
3. Click "New +" → Web Service
4. Select your repo
5. Settings:
   - Name: `hoxobil-store`
   - Build Command: (auto from render.yaml)
   - Start Command: (auto from render.yaml)
6. Click "Create Web Service"
7. Wait for build (5-10 mins)
8. Add PostgreSQL database:
   - Dashboard → "New +" → PostgreSQL
   - Name: `hoxobil-postgres`
9. Copy DATABASE_URL to env vars
10. Set all other env vars in dashboard
11. Manual deploy or auto-redeploy

#### For Heroku
```bash
heroku login
heroku create hoxobil-store
heroku addons:create heroku-postgresql:standard-0 -a hoxobil-store
heroku config:set DEBUG=False -a hoxobil-store
heroku config:set SECRET_KEY=@ei9+r-v18zbkm@$!*le8j!l2w-k2)q&$=q4h3k&^=fbm%3wgx -a hoxobil-store
# Set all other env vars...
git push heroku main
```

#### For DigitalOcean
1. Create account at https://digitalocean.com
2. Go to App Platform → Create App
3. Connect GitHub repo
4. Configure:
   - Build Command: `pip install -r requirements.txt && python manage.py migrate && python manage.py collectstatic --noinput`
   - Run Command: `gunicorn hoxobil_store.wsgi:application`
5. Add PostgreSQL database from same panel
6. Set environment variables
7. Deploy

### Step 5: Point Your Domain
1. Get deployment URL from platform (e.g., `hoxobil-store.onrender.com`)
2. Go to your domain registrar
3. Update DNS:
   - CNAME: `www` → `hoxobil-store.onrender.com`
   - A: `@` → (platform's IP if needed)
4. In platform settings, add custom domain
5. Wait for DNS propagation (5-30 mins)

### Step 6: Verify It Works
```
✓ Visit https://yourdomain.com
✓ Homepage loads
✓ Browse products
✓ Add to cart
✓ Try checkout
✓ Admin at /admin/
```

---

## Checklists

### Before Deployment
- [ ] Generated new SECRET_KEY: `@ei9+r-v18zbkm@$!*le8j!l2w-k2)q&$=q4h3k&^=fbm%3wgx`
- [ ] Updated .env.production with all values
- [ ] Got production payment API keys
- [ ] Set up email (Gmail App Password or SendGrid)
- [ ] Registered domain
- [ ] Tested locally with DEBUG=False

### After Deployment
- [ ] Homepage loads over HTTPS
- [ ] Static files display (images, CSS)
- [ ] Can view products
- [ ] Can add to cart
- [ ] Payment page shows
- [ ] Admin panel works
- [ ] Test payment processed
- [ ] Email received (if configured)

---

## Troubleshooting

### Site won't load
```bash
# Check logs in platform dashboard
# Render: Logs tab
# Heroku: heroku logs --tail
# DigitalOcean: Logs tab

# Common issues:
# - Missing environment variable
# - Database not set
# - DEBUG still True
```

### Static files missing (images blank)
```bash
# Run in platform (usually auto, but if not):
python manage.py collectstatic --noinput

# Render: Re-deploy
# Heroku: heroku run python manage.py collectstatic
```

### Database errors
```bash
# Verify DATABASE_URL format
# postgresql://user:password@host:5432/dbname

# Run migrations
python manage.py migrate

# For Heroku:
heroku run python manage.py migrate
```

### Email not sending
```
✓ Check EMAIL_HOST (smtp.gmail.com for Gmail)
✓ Check EMAIL_HOST_USER (your email)
✓ Check EMAIL_HOST_PASSWORD (Gmail App Password, not regular password)
✓ For Gmail: Enable 2FA, create App Password
✓ PORT should be 587
✓ USE_TLS should be True
```

### Payment not working
```
✓ Using PRODUCTION keys, not test keys
✓ PAYSTACK_MODE=live (not test)
✓ Keys match what's in payment gateway dashboard
✓ Payment webhook URLs configured
```

---

## Files Your Project Has

- ✅ `.env` - Development settings (DEBUG=False, keys fixed)
- ✅ `.env.production.example` - Production template to copy
- ✅ `render.yaml` - Render.com ready
- ✅ `Procfile` - Heroku ready
- ✅ `hoxobil_store/settings.py` - Security hardened
- ✅ `requirements.txt` - Python 3.14 compatible
- ✅ `DEPLOYMENT_READY.md` - Full status report
- ✅ `DEPLOYMENT_GUIDE.md` - Detailed instructions
- ✅ `PRE_DEPLOYMENT_CHECKLIST.md` - Step-by-step checklist

---

## Commands You'll Need

```bash
# Generate new SECRET_KEY if needed
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# Test locally with production settings
DEBUG=False python manage.py runserver

# Check deployment readiness
python manage.py check --deploy

# Collect static files
python manage.py collectstatic --noinput

# Run migrations on deployment
# Platform specific - see your dashboard
```

---

## Support Links

- **Render Docs**: https://render.com/docs
- **Heroku Docs**: https://devcenter.heroku.com
- **DigitalOcean**: https://docs.digitalocean.com
- **Django Deployment**: https://docs.djangoproject.com/en/stable/howto/deployment/
- **Flutterwave API**: https://developer.flutterwave.com
- **Paystack API**: https://paystack.com/docs

---

## That's It!

Your site should be live in 15-30 minutes. If stuck, check:
1. Platform logs
2. Environment variables in dashboard
3. Database connection
4. Django check output

**Questions?** See DEPLOYMENT_GUIDE.md for detailed help.

🎉 Good luck with your deployment!
