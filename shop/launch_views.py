import logging
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

import requests as http_requests

from .models import Donation

logger = logging.getLogger(__name__)


def _is_launched():
    return timezone.now() >= settings.LAUNCH_DATE


def _donation_progress():
    """Shared helper so the page render and the polling API return the same numbers."""
    total_raised = Donation.objects.filter(status='SUCCESSFUL').aggregate(
        total=Sum('amount')
    )['total'] or Decimal('0.00')

    goal = settings.DONATION_GOAL_NGN
    percent = float(min(Decimal('100'), (total_raised / goal) * Decimal('100'))) if goal else 0

    return {
        'total_raised': total_raised,
        'goal': goal,
        'percent': round(percent, 1),
    }


# ─────────────────────────────────────────────────────────
#  LAUNCH PAGE
# ─────────────────────────────────────────────────────────
def launch_page(request):
    if _is_launched():
        return redirect('shop:home')

    progress = _donation_progress()

    return render(request, 'shop/launch.html', {
        'launch_date': settings.LAUNCH_DATE,
        'total_raised': progress['total_raised'],
        'goal': progress['goal'],
        'percent': progress['percent'],
        'flutterwave_public_key': settings.FLUTTERWAVE_PUBLIC_KEY,
        'paystack_public_key': settings.PAYSTACK_PUBLIC_KEY,
    })


def donation_progress_api(request):
    """Polled by launch.html to update the progress line without a full reload."""
    progress = _donation_progress()
    return JsonResponse({
        'total_raised': str(progress['total_raised']),
        'goal': str(progress['goal']),
        'percent': progress['percent'],
    })


# ─────────────────────────────────────────────────────────
#  SHARED VALIDATION
# ─────────────────────────────────────────────────────────
def _validate_donation_amount(request):
    raw_amount = request.POST.get('amount', '').strip()
    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, TypeError):
        return None, "Please enter a valid donation amount."

    if amount <= 0:
        return None, "Donation amount must be greater than zero."

    # Sanity ceiling — adjust if you expect larger single donations.
    if amount > Decimal('10000000'):
        return None, "That amount looks too large — please contact us directly for major gifts."

    return amount, None


# ─────────────────────────────────────────────────────────
#  FLUTTERWAVE — GUEST DONATION
# ─────────────────────────────────────────────────────────
@require_POST
def donate_initiate_flutterwave(request):
    amount, error = _validate_donation_amount(request)
    if error:
        return JsonResponse({'status': 'error', 'message': error}, status=400)

    name = request.POST.get('name', '').strip()
    email = request.POST.get('email', '').strip()

    reference = f"HOXOBIL-DONATE-FW-{uuid.uuid4().hex[:12]}"

    Donation.objects.create(
        amount=amount,
        name=name,
        email=email,
        provider='FLUTTERWAVE',
        reference=reference,
        status='PENDING',
    )

    callback_url = request.build_absolute_uri('/donate/flutterwave/callback/')

    return JsonResponse({
        'status': 'ok',
        'reference': reference,
        'amount': str(amount),
        'currency': 'NGN',
        'callback_url': callback_url,
        'name': name or 'Anonymous',
        'email': email or 'donor@hoxobil.com',
    })


def donate_flutterwave_callback(request):
    status = request.GET.get('status')
    transaction_id = request.GET.get('transaction_id')
    tx_ref = request.GET.get('tx_ref')

    if status == 'cancelled':
        messages.warning(request, "Donation was cancelled.")
        return redirect('shop:launch_page')

    if status != 'successful' or not transaction_id or not tx_ref:
        messages.error(request, "Donation was not completed. Please try again.")
        return redirect('shop:launch_page')

    try:
        donation = Donation.objects.get(reference=tx_ref, provider='FLUTTERWAVE')
    except Donation.DoesNotExist:
        logger.error("donate_flutterwave_callback | Unknown reference: %s", tx_ref)
        messages.error(request, "We couldn't find that donation. Please contact support.")
        return redirect('shop:launch_page')

    if donation.status == 'SUCCESSFUL':
        messages.success(request, "Thank you again for your donation! 🙏")
        return redirect('shop:launch_page')

    try:
        verify_url = f"https://api.flutterwave.com/v3/transactions/{transaction_id}/verify"
        headers = {
            "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
            "Content-Type": "application/json",
        }
        response = http_requests.get(verify_url, headers=headers, timeout=15)
        data = response.json()

        if data.get('status') != 'success' or data.get('data', {}).get('status') != 'successful':
            logger.error("donate_flutterwave_callback | Verification failed for %s: %s", tx_ref, data)
            donation.status = 'FAILED'
            donation.save(update_fields=['status'])
            messages.error(request, "Donation verification failed. Please contact support.")
            return redirect('shop:launch_page')

        paid_amount = Decimal(str(data['data']['amount']))
        paid_currency = data['data']['currency']

        if paid_currency != 'NGN' or paid_amount < donation.amount:
            logger.error(
                "donate_flutterwave_callback | Amount mismatch for %s | expected %s NGN, got %s %s",
                tx_ref, donation.amount, paid_amount, paid_currency
            )
            donation.status = 'FAILED'
            donation.save(update_fields=['status'])
            messages.error(request, "Donation amount mismatch. Please contact support.")
            return redirect('shop:launch_page')

    except Exception as e:
        logger.error("donate_flutterwave_callback | Verification error for %s: %s", tx_ref, e)
        messages.error(request, "Could not verify donation. Please contact support.")
        return redirect('shop:launch_page')

    donation.status = 'SUCCESSFUL'
    donation.verified_at = timezone.now()
    donation.save(update_fields=['status', 'verified_at'])

    messages.success(request, "Thank you for your donation! 🙏 We're one step closer to launch.")
    return redirect('shop:launch_page')


# ─────────────────────────────────────────────────────────
#  PAYSTACK — GUEST DONATION
# ─────────────────────────────────────────────────────────
@require_POST
def donate_initiate_paystack(request):
    amount, error = _validate_donation_amount(request)
    if error:
        return JsonResponse({'status': 'error', 'message': error}, status=400)

    name = request.POST.get('name', '').strip()
    email = request.POST.get('email', '').strip()

    reference = f"HOXOBIL-DONATE-PS-{uuid.uuid4().hex[:12]}"

    Donation.objects.create(
        amount=amount,
        name=name,
        email=email,
        provider='PAYSTACK',
        reference=reference,
        status='PENDING',
    )

    callback_url = request.build_absolute_uri('/donate/paystack/callback/')

    # Paystack takes amount in kobo (smallest subunit).
    amount_kobo = int((amount * Decimal('100')).to_integral_value())

    return JsonResponse({
        'status': 'ok',
        'reference': reference,
        'amount_kobo': amount_kobo,
        'currency': 'NGN',
        'callback_url': callback_url,
        'email': email or 'donor@hoxobil.com',
    })


def donate_paystack_callback(request):
    reference = request.GET.get('reference') or request.GET.get('trxref')

    if not reference:
        messages.error(request, "Donation was not completed. Please try again.")
        return redirect('shop:launch_page')

    try:
        donation = Donation.objects.get(reference=reference, provider='PAYSTACK')
    except Donation.DoesNotExist:
        logger.error("donate_paystack_callback | Unknown reference: %s", reference)
        messages.error(request, "We couldn't find that donation. Please contact support.")
        return redirect('shop:launch_page')

    if donation.status == 'SUCCESSFUL':
        messages.success(request, "Thank you again for your donation! 🙏")
        return redirect('shop:launch_page')

    try:
        verify_url = f"https://api.paystack.co/transaction/verify/{reference}"
        headers = {
            "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
            "Content-Type": "application/json",
        }
        response = http_requests.get(verify_url, headers=headers, timeout=15)
        data = response.json()

        if not data.get('status') or data.get('data', {}).get('status') != 'success':
            logger.error("donate_paystack_callback | Verification failed for %s: %s", reference, data)
            donation.status = 'FAILED'
            donation.save(update_fields=['status'])
            messages.error(request, "Donation verification failed. Please contact support.")
            return redirect('shop:launch_page')

        paid_amount = Decimal(str(data['data']['amount'])) / Decimal('100')
        paid_currency = data['data']['currency']

        if paid_currency != 'NGN' or paid_amount < donation.amount:
            logger.error(
                "donate_paystack_callback | Amount mismatch for %s | expected %s NGN, got %s %s",
                reference, donation.amount, paid_amount, paid_currency
            )
            donation.status = 'FAILED'
            donation.save(update_fields=['status'])
            messages.error(request, "Donation amount mismatch. Please contact support.")
            return redirect('shop:launch_page')

    except Exception as e:
        logger.error("donate_paystack_callback | Verification error for %s: %s", reference, e)
        messages.error(request, "Could not verify donation. Please contact support.")
        return redirect('shop:launch_page')

    donation.status = 'SUCCESSFUL'
    donation.verified_at = timezone.now()
    donation.save(update_fields=['status', 'verified_at'])

    messages.success(request, "Thank you for your donation! 🙏 We're one step closer to launch.")
    return redirect('shop:launch_page')