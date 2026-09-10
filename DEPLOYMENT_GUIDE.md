# Production Deployment Guide for Hoxobil Store

## Current Status

### ✓ What's Already Good
- Database configuration supports production databases (PostgreSQL)
- Security middleware properly configured
- Static files configured with WhiteNoise
- CORS properly configured for development
- Email backend prepared for production
- Session and CSRF cookies configured securely

### ⚠️ Issues Found

Django deployment check found 2 issues:
1. **security.W004**: Missing SECURE_HSTS_SECONDS setting
2. **security.W009**: Weak SECRET_KEY (using default insecure key)

---

## Step 1: Generate a Secure SECRET_KEY

Run this command to generate a new, production-ready SECRET_KEY:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Copy the output and replace the SECRET_KEY in your `.env.production` file.

---

## Step 2: Update Settings for Production

Create or update your `.env` file for production deployment:

```
DEBUG=False
SECRET_KEY=<your-newly-generated-key>
SECURE_HSTS_SECONDS=31536000
RENDER_EXTERNAL_HOSTNAME=your-domain.com
CORS_ALLOWED_ORIGINS=https://hoxobil.store,https://www.hoxobil.store
DATABASE_URL=postgresql://user:password@localhost:5432/hoxobil_db
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
EMAIL_USE_TLS=True
FLUTTERWAVE_PUBLIC_KEY=FLWPUBK-<your-production-key>
FLUTTERWAVE_SECRET_KEY=FLWSECK-<your-production-key>
PAYSTACK_PUBLIC_KEY=pk_live_<your-key>
PAYSTACK_SECRET_KEY=sk_live_<your-key>
PAYSTACK_MODE=live
PRINTFUL_ACCESS_TOKEN=<your-token>
PRINTFUL_STORE_ID=<your-store-id>
ANTHROPIC_API_KEY=<your-key>
SERPER_API_KEY=<your-key>
PUBLIC_BASE_URL=https://hoxobil.store
```

---

## Step 3: Update settings.py for Additional Security

Add this to your `hoxobil_store/settings.py` in the production settings block:

```python
if not DEBUG:
    # ... existing security settings ...
    
    # HSTS (HTTP Strict Transport Security)
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    
    # Additional security
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_SECURITY_POLICY = {
        "default-src": ("'self'",),
    }
```

---

## Step 4: Database Migration

### Option A: PostgreSQL (Recommended)
```bash
# 1. Install PostgreSQL and create database
# 2. Set DATABASE_URL in .env
# 3. Run migrations
python manage.py migrate

# 4. Create superuser
python manage.py createsuperuser
```

### Option B: Keep SQLite (Not Recommended for Production)
```bash
# Just run migrations
python manage.py migrate
```

---

## Step 5: Collect Static Files

```bash
python manage.py collectstatic --noinput
```

---

## Step 6: Deployment Platform Setup

### Render.com (Recommended)
1. Create `render.yaml` in root directory:

```yaml
services:
  - type: web
    name: hoxobil-store
    runtime: python
    pythonVersion: "3.12"
    buildCommand: pip install -r requirements.txt && python manage.py migrate && python manage.py collectstatic --noinput
    startCommand: gunicorn hoxobil_store.wsgi:application
    envVars:
      - key: DEBUG
        value: "False"
      - key: SECRET_KEY
        sync: false
      - key: DATABASE_URL
        sync: false
      - key: RENDER_EXTERNAL_HOSTNAME
        value: "${{ RENDER_EXTERNAL_HOSTNAME }}"
      # Add other env vars here
```

2. Connect your GitHub repo to Render
3. Set environment variables in Render dashboard
4. Deploy!

### Heroku
```bash
# 1. Install Heroku CLI
# 2. Login
heroku login

# 3. Create app
heroku create hoxobil-store

# 4. Set environment variables
heroku config:set DEBUG=False
heroku config:set SECRET_KEY=<your-key>
heroku config:set DATABASE_URL=<postgres-url>
# ... set all other vars

# 5. Deploy
git push heroku main
```

### Other Platforms
- **DigitalOcean App Platform**: Similar to Render, use buildpacks
- **AWS**: Use Elastic Beanstalk or EC2 + RDS
- **PythonAnywhere**: Simple WSGI deployment

---

## Step 7: Domain & SSL Setup

1. **Domain Setup**:
   - Point your domain DNS to deployment platform
   - Usually provided in platform dashboard

2. **SSL Certificate**:
   - Most platforms (Render, Heroku, etc.) provide free SSL
   - Usually automatic and renewed automatically
   - Verify in platform settings

---

## Step 8: Email Configuration

### Gmail (Using App Password)
```
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=<16-char-app-password>
EMAIL_USE_TLS=True
```

### SendGrid
```
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=587
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=<SendGrid API Key>
EMAIL_USE_TLS=True
```

---

## Step 9: Payment Gateway Setup

### Flutterwave
1. Create production merchant account at flutterwave.com
2. Get production API keys
3. Set `FLUTTERWAVE_PUBLIC_KEY` and `FLUTTERWAVE_SECRET_KEY`
4. Test payment flow in production

### Paystack
1. Create production merchant account at paystack.co
2. Get live API keys (not test keys!)
3. Set `PAYSTACK_PUBLIC_KEY` and `PAYSTACK_SECRET_KEY`
4. Set `PAYSTACK_MODE=live`

---

## Step 10: Pre-Deployment Testing

```bash
# 1. Run all checks
python manage.py check --deploy

# 2. Run tests
python manage.py test

# 3. Test with DEBUG=False locally
DEBUG=False python manage.py runserver

# 4. Test critical flows:
#    - User registration
#    - Product browsing
#    - Shopping cart
#    - Payment processing
#    - Email notifications
#    - Admin panel access
```

---

## Step 11: Post-Deployment Verification

1. **Test in Production**:
   - Visit homepage
   - Browse products
   - Test payment with test card
   - Verify email notifications
   - Check admin panel

2. **Monitor Logs**:
   - Check application logs for errors
   - Set up error tracking (Sentry recommended)

3. **Monitor Performance**:
   - Use platform's monitoring tools
   - Set up alerting for errors

---

## Step 12: Backup & Recovery

1. **Database Backups**:
   - Most platforms include automatic backups
   - Set retention to at least 30 days
   - Test restore procedure

2. **Media Files**:
   - Consider using cloud storage (S3, Cloudflare R2, etc.)
   - Keep local backup of important product images

---

## Useful Commands

```bash
# Check current settings
python manage.py check

# Verify deployment readiness
python manage.py check --deploy

# Run production-like mode locally
DEBUG=False python manage.py runserver 0.0.0.0:8000

# Create database backup
python manage.py dumpdata > backup.json

# Restore from backup
python manage.py loaddata backup.json

# View secret from environment
echo $SECRET_KEY

# Generate new key
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## Security Checklist

- [ ] DEBUG = False
- [ ] SECRET_KEY is strong (50+ chars, random, no default)
- [ ] All API keys stored in environment (not in code)
- [ ] HTTPS/SSL enabled
- [ ] HSTS enabled
- [ ] SECURE_COOKIE settings enabled
- [ ] ALLOWED_HOSTS configured correctly
- [ ] CORS_ALLOWED_ORIGINS restricted
- [ ] Email configured
- [ ] Database is production-grade (PostgreSQL)
- [ ] Static files collected and served
- [ ] Backups configured
- [ ] Error logging set up
- [ ] All tests passing
- [ ] Payment gateways tested in production mode

---

## Troubleshooting

### Static files not loading
```bash
python manage.py collectstatic --clear --noinput
```

### Database errors
- Verify DATABASE_URL format
- Check database is running
- Verify credentials are correct
- Run migrations: `python manage.py migrate`

### Payment errors
- Verify API keys are production keys (not test)
- Check payment webhook settings
- Enable debug logging for payment module

### Email not sending
- Verify EMAIL_HOST settings
- Check EMAIL_HOST_PASSWORD is correct
- For Gmail, use App Password (2FA required)
- Check firewall/port restrictions (587)

---

## Support & Documentation

- Django Deployment: https://docs.djangoproject.com/en/stable/howto/deployment/
- Your platform's documentation (Render, Heroku, etc.)
- Payment gateway documentation
- This project's issue tracker
