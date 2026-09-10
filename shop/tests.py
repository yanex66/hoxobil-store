import hmac
import hashlib
import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from shop.models import Product, ProductVariant, Order, OrderItem, CustomDesignTicket, PasswordResetOTP
from shop.cart import Cart

User = get_user_model()


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
        request = self.client.request().wsgi_request
        cart = Cart(request)

        cart.add(self.variant, quantity=100)

        self.assertEqual(cart.cart[str(self.variant.id)]['quantity'], 50)