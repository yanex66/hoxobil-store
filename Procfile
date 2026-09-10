release: python manage.py migrate && python manage.py collectstatic --noinput
web: gunicorn hoxobil_store.wsgi:application --log-file -
