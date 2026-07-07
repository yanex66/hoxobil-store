# ─────────────────────────────────────────────────────────
# SAVE AS: shop/launch_middleware.py
# ─────────────────────────────────────────────────────────
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse, NoReverseMatch
from django.utils import timezone


class LaunchGateMiddleware:
    """
    Redirects every visitor to the /launch/ coming-soon page until
    settings.LAUNCH_DATE. Staff/superusers browse the real site normally
    at any time, so you can keep testing and adding products before launch.

    Always allowed, launch date or not:
      - the launch page itself (shop:launch_page) and its donation endpoints
      - /admin/, /static/, /media/ (see LAUNCH_ALLOWED_PATH_PREFIXES)
    """

    ALWAYS_ALLOWED_URL_NAMES = {
        'shop:launch_page',
        'shop:donate_initiate_flutterwave',
        'shop:donate_initiate_paystack',
        'shop:donate_flutterwave_callback',
        'shop:donate_paystack_callback',
        'shop:donation_progress_api',
    }

    def __init__(self, get_response):
        self.get_response = get_response
        self._allowed_paths = None  # resolved lazily, once urls are loaded

    def _get_allowed_paths(self):
        if self._allowed_paths is None:
            paths = set()
            for name in self.ALWAYS_ALLOWED_URL_NAMES:
                try:
                    paths.add(reverse(name))
                except NoReverseMatch:
                    pass
            self._allowed_paths = paths
        return self._allowed_paths

    def __call__(self, request):
        if self._is_locked(request):
            return redirect(reverse('shop:launch_page'))
        return self.get_response(request)

    def _is_locked(self, request):
        if timezone.now() >= settings.LAUNCH_DATE:
            return False

        if request.user.is_authenticated and request.user.is_staff:
            return False

        path = request.path

        for prefix in getattr(settings, 'LAUNCH_ALLOWED_PATH_PREFIXES', []):
            if path.startswith(prefix):
                return False

        if path in self._get_allowed_paths():
            return False

        return True