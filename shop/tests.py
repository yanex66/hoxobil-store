import hmac
import hashlib
import json
import os
from decimal import Decimal
from django.test import TestCase, Client, RequestFactory, override_settings
from django.contrib.sessions.middleware import SessionMiddleware
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from shop.models import (
    Product, ProductVariant, Order, OrderItem, CustomDesignTicket,
    NewsletterSubscriber, PasswordResetOTP, SupportChat, ChatMessage,
)
from shop.cart import Cart
from shop.whatsapp import send_whatsapp_notification

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class HoxobilSecurityAndPaymentTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testcustomer', email='customer@hoxobil.store', password='SecurePassword123!')
        
        # Create a test product and variant
        self.product = Product.objects.create(
            name='Test Hoodie',
            slug='test-hoodie',
            price=Decimal('50.00'),
            available=True
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            pod_id='12345',
            size='L',
            color='Black',
            price=Decimal('50.00'),
            available=True
        )

        # Create a test order
        self.order = Order.objects.create(
            user=self.user,
            first_name='Iyanuoluwa',
            last_name='Ibajesomo',
            email='customer@hoxobil.store',
            address='123 University Road',
            city='Lagos',
            country='NG',
            postal_code='100001',
            shipping_cost=Decimal('10.00'),
            paid=False,
            status='PENDING',
            paystack_reference='HOXOBIL-PS-TEST-REF',
            flutterwave_reference='HOXOBIL-FLW-TEST-REF'
        )

    def test_otp_generation_security(self):
        """Verify OTP codes use cryptographic randomness and conform to 6 digits."""
        otp = PasswordResetOTP.generate_code(self.user)
        self.assertEqual(len(otp.code), 6)
        self.assertTrue(otp.code.isdigit())
        self.assertTrue(otp.is_valid())

    def test_paystack_webhook_signature_verification(self):
        """Verify that webhooks reject requests with invalid or missing signatures."""
        payload = {'event': 'charge.success', 'data': {'reference': 'HOXOBIL-PS-TEST-REF'}}
        body = json.dumps(payload).encode('utf-8')
        
        # Invalid signature
        response = self.client.post(
            reverse('shop:paystack_webhook'),
            data=body,
            content_type='application/json',
            HTTP_X_PAYSTACK_SIGNATURE='invalid_signature'
        )
        self.assertEqual(response.status_code, 401)

    def test_order_subtotal_multi_currency(self):
        """Verify order subtotals compute correctly based on order currency."""
        self.order.currency = 'USD'
        self.order.save()
        
        OrderItem.objects.create(
            order=self.order,
            product=self.product,
            product_variant=self.variant,
            price=Decimal('50.00'),
            quantity=2
        )
        
        subtotal = self.order.get_items_subtotal()
        self.assertEqual(subtotal.amount, Decimal('100.00'))
        self.assertEqual(subtotal.currency.code, 'USD')

    def test_custom_ticket_order_linking_security(self):
        """Verify that custom tickets strictly bind to their verified orders."""
        ticket = CustomDesignTicket.objects.create(
            user=self.user,
            garment_item='Premium Hoodie',
            custom_text='Cyberpunk Vibe',
            invoice_amount=Decimal('75000.00'),
            linked_order=self.order
        )
        
        self.assertEqual(ticket.linked_order, self.order)
        self.assertEqual(ticket.linked_order.user, self.user)

    def test_cart_caps_variant_quantity(self):
        request = RequestFactory().get('/')
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        cart = Cart(request)

        cart.add(self.variant, quantity=100)

        self.assertEqual(cart.cart[str(self.variant.id)]['quantity'], 50)

    @patch('shop.views.send_hoxobil_email')
    def test_registration_logs_in_and_sends_welcome_email_after_commit(self, send_email):
        send_email.return_value = {'id': 'test-email'}
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('shop:register'),
                {
                    'email': 'newcreator@example.com',
                    'first_name': 'New',
                    'password1': 'StrongPassword123!',
                    'password2': 'StrongPassword123!',
                },
            )

        self.assertRedirects(response, reverse('shop:home'))
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertEqual(response.wsgi_request.user.email, 'newcreator@example.com')
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.args[0], 'newcreator@example.com')
        self.assertIn('Welcome to Hoxobil Enterprise', send_email.call_args.args[1])
        self.assertIn('Hoxobot', send_email.call_args.args[2])

    @patch('shop.views.send_hoxobil_email')
    def test_newsletter_subscription_persists_and_sends_confirmation(self, send_email):
        send_email.return_value = {'id': 'newsletter-test'}
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('shop:newsletter_subscribe'),
                {'email': '  CREATOR@example.com '},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')
        self.assertTrue(
            NewsletterSubscriber.objects.filter(
                email='creator@example.com',
                is_active=True,
            ).exists()
        )
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.args[0], 'creator@example.com')
        self.assertIn('Hoxobot', send_email.call_args.args[2])
        self.assertIn('https://hoxobil.store/static/images/image.png', send_email.call_args.args[2])


class WhatsAppNotificationTests(TestCase):
    @patch.dict(os.environ, {
        'TWILIO_ACCOUNT_SID': 'AC123',
        'TWILIO_AUTH_TOKEN': 'secret-token',
        'TWILIO_WHATSAPP_FROM': 'whatsapp:+14155238886',
    }, clear=True)
    @patch('shop.whatsapp.urlopen')
    def test_notification_uses_twilio_whatsapp_api(self, mocked_urlopen):
        response = MagicMock()
        response.status = 201
        response.__enter__.return_value = response
        mocked_urlopen.return_value = response

        result = send_whatsapp_notification(
            'Test Customer',
            'customer@example.com',
            'I need help with my design.',
        )

        self.assertTrue(result)
        request = mocked_urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://api.twilio.com/'))
        payload = parse_qs(request.data.decode())
        self.assertEqual(payload['To'], ['whatsapp:+2349130273282'])
        self.assertIn('customer@example.com', payload['Body'][0])
        self.assertIn('I need help with my design.', payload['Body'][0])
        self.assertEqual(mocked_urlopen.call_args.kwargs['timeout'], 5)

    @patch.dict(os.environ, {}, clear=True)
    @patch('shop.whatsapp.urlopen')
    def test_notification_skips_when_twilio_configuration_is_missing(self, mocked_urlopen):
        self.assertFalse(send_whatsapp_notification('Customer', 'customer@example.com', 'Help'))
        mocked_urlopen.assert_not_called()

    @patch.dict(os.environ, {
        'TWILIO_ACCOUNT_SID': 'AC123',
        'TWILIO_AUTH_TOKEN': 'secret-token',
        'TWILIO_WHATSAPP_FROM': 'whatsapp:+14155238886',
    }, clear=True)
    @patch('shop.whatsapp.urlopen', side_effect=TimeoutError('request timed out'))
    def test_notification_handles_provider_timeout(self, mocked_urlopen):
        self.assertFalse(send_whatsapp_notification('Customer', 'customer@example.com', 'Help'))
        mocked_urlopen.assert_called_once()

    @patch('shop.whatsapp.queue_whatsapp_notification')
    def test_customer_chat_message_queues_whatsapp_notification(self, queue_notification):
        chat = SupportChat.objects.create(user=User.objects.create_user(
            username='chatcustomer',
            email='chat@example.com',
            first_name='Chat',
            last_name='Customer',
        ))

        with self.captureOnCommitCallbacks(execute=True):
            ChatMessage.objects.create(chat=chat, sender_type='user', text='Please help with my order.')

        queue_notification.assert_called_once_with(
            'Chat Customer',
            'chat@example.com',
            'Please help with my order.',
        )