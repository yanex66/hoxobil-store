# Deployment Checklist for Hoxobil Store

## Critical Fixes (Before Deployment)

### 1. Environment Variables
- [ ] Set `DEBUG=False` 
- [ ] Set `DATABASE_URL` for production database (PostgreSQL recommended)
- [ ] Set `SECRET_KEY` to a secure, randomly generated key
- [ ] Set `RENDER_EXTERNAL_HOSTNAME` if deploying to Render.com
- [ ] Set `CORS_ALLOWED_ORIGINS` (e.g., `https://hoxobil.store,https://www.hoxobil.store`)
- [ ] Configure email settings:
  - `EMAIL_HOST` (e.g., smtp.gmail.com, smtp.sendgrid.net)
  - `EMAIL_HOST_USER`
  - `EMAIL_HOST_PASSWORD`
  - `EMAIL_PORT`
  - `EMAIL_USE_TLS`

### 2. Sensitive Keys Management
- [ ] Remove or redact API keys from `.env` (use deployment platform's secrets)
- [ ] Rotate all exposed keys:
  - Flutterwave public/secret keys
  - Paystack public/secret keys
  - Printful access token
  - Anthropic API key
  - Serper API key
- [ ] Use deployment platform's environment variable manager (Render.com, Heroku, AWS, etc.)

### 3. Database
- [ ] Migrate to production database (PostgreSQL)
- [ ] Run migrations: `python manage.py migrate`
- [ ] Create superuser if needed: `python manage.py createsuperuser`
- [ ] Backup database before deployment

### 4. Static Files
- [ ] Run `python manage.py collectstatic --noinput`
- [ ] Configure CDN/cloud storage if needed (S3, Cloudflare, etc.)
- [ ] Verify static files are served correctly

### 5. Security Checks
- [ ] Run `python manage.py check --deploy` to find issues
- [ ] Set `SECURE_HSTS_SECONDS` if using HTTPS (production)
- [ ] Enable `SECURE_HSTS_INCLUDE_SUBDOMAINS` and `SECURE_HSTS_PRELOAD`
- [ ] Review ALLOWED_HOSTS (remove development hosts)
- [ ] Review CSRF_TRUSTED_ORIGINS (remove local/ngrok tunnels)

### 6. Testing
- [ ] Test payment processing (Paystack & Flutterwave)
- [ ] Test email notifications
- [ ] Test product creation and orders
- [ ] Test admin panel access
- [ ] Verify SSL/HTTPS configuration

### 7. Monitoring & Logging
- [ ] Set up error logging (Sentry recommended)
- [ ] Configure application monitoring
- [ ] Set up uptime monitoring
- [ ] Enable access logs

### 8. Deployment Platform
- [ ] Choose platform (Render.com, Heroku, DigitalOcean, AWS, etc.)
- [ ] Create deployment configuration file (render.yaml, Procfile, etc.)
- [ ] Set up automatic deployments (optional)
- [ ] Configure domain DNS
- [ ] Set up SSL certificate (usually automatic)

## Quick Fix for Now

### 1. Set DEBUG to False (Critical)
```bash
# In .env, change:
DEBUG=False
```

### 2. Fix Flutterwave Secret Key
The key appears corrupted. Replace with correct production key.

### 3. Prepare Production Environment File
Create a `.env.production` template with placeholders for sensitive values.

## Deployment Commands

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Collect static files
python manage.py collectstatic --noinput

# 3. Run migrations
python manage.py migrate

# 4. Run deployment checks
python manage.py check --deploy

# 5. Test locally with production settings (if possible)
DEBUG=False python manage.py runserver
```

## Post-Deployment

- [ ] Monitor application logs
- [ ] Test all critical user flows
- [ ] Verify payment processing
- [ ] Check email delivery
- [ ] Monitor performance and errors
- [ ] Set up backup schedules
