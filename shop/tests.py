import hmac
import hashlib
import base64
import json
import os
import tempfile
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
    BotKnowledge, UnknownQuestion,
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
    def _twilio_signature(self, url, payload, auth_token):
        signed_data = url + ''.join(
            key + value
            for key in sorted(payload)
            for value in (payload[key] if isinstance(payload[key], list) else [payload[key]])
        )
        digest = hmac.new(auth_token.encode(), signed_data.encode(), hashlib.sha1).digest()
        return base64.b64encode(digest).decode()

    @patch.dict(os.environ, {
        'TWILIO_ACCOUNT_SID': 'AC123',
        'TWILIO_AUTH_TOKEN': 'secret-token',
        'TWILIO_WHATSAPP_FROM': 'whatsapp:+14155238886',
    }, clear=True)
    @patch('shop.whatsapp.urlopen')
    @override_settings(PUBLIC_BASE_URL='https://hoxobil.store')
    def test_notification_uses_twilio_whatsapp_api(self, mocked_urlopen):
        response = MagicMock()
        response.status = 201
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({'sid': 'SM' + '1' * 32}).encode()
        mocked_urlopen.return_value = response

        result = send_whatsapp_notification(
            'Test Customer',
            'customer@example.com',
            'I need help with my design.',
            chat_id=34,
        )

        self.assertEqual(result, 'SM' + '1' * 32)
        request = mocked_urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://api.twilio.com/'))
        payload = parse_qs(request.data.decode())
        self.assertEqual(payload['To'], ['whatsapp:+2349130273282'])
        self.assertIn('customer@example.com', payload['Body'][0])
        self.assertIn('I need help with my design.', payload['Body'][0])
        self.assertIn('https://hoxobil.store/admin/shop/chat/34/', payload['Body'][0])
        self.assertEqual(mocked_urlopen.call_args.kwargs['timeout'], 5)

    @patch.dict(os.environ, {
        'TWILIO_ACCOUNT_SID': 'AC123',
        'TWILIO_AUTH_TOKEN': 'secret-token',
        'TWILIO_WHATSAPP_FROM': 'whatsapp:+14155238886',
    }, clear=True)
    @patch('shop.whatsapp.urlopen')
    def test_notification_stores_returned_sid_on_customer_message(self, mocked_urlopen):
        response = MagicMock()
        response.status = 201
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({'sid': 'SM' + '3' * 32}).encode()
        mocked_urlopen.return_value = response
        chat = SupportChat.objects.create(user=User.objects.create_user(
            username='sidcustomer',
            email='sid@example.com',
        ))
        message = ChatMessage.objects.create(
            chat=chat,
            sender_type='user',
            text='Please contact me about my order.',
        )

        result = send_whatsapp_notification(
            'SID Customer',
            'sid@example.com',
            message.text,
            message_id=message.pk,
        )

        self.assertEqual(result, 'SM' + '3' * 32)
        message.refresh_from_db()
        self.assertEqual(message.twilio_message_sid, 'SM' + '3' * 32)

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
            message_id=ChatMessage.objects.get(chat=chat).pk,
            chat_id=chat.pk,
        )

    @override_settings(SECURE_SSL_REDIRECT=False)
    @patch.dict(os.environ, {
        'TWILIO_AUTH_TOKEN': 'webhook-secret',
        'TWILIO_WHATSAPP_ADMIN_NUMBER': '+2349130273282',
    }, clear=True)
    @patch('shop.whatsapp.queue_whatsapp_notification')
    def test_twilio_webhook_saves_admin_reply_to_most_recent_customer_chat(self, queue_notification):
        earlier_chat = SupportChat.objects.create(user=User.objects.create_user(
            username='earliercustomer',
            email='earlier@example.com',
        ))
        ChatMessage.objects.create(chat=earlier_chat, sender_type='user', text='Earlier question')

        latest_chat = SupportChat.objects.create(user=User.objects.create_user(
            username='latestcustomer',
            email='latest@example.com',
        ))
        latest_customer_message = ChatMessage.objects.create(
            chat=latest_chat,
            sender_type='user',
            text='Latest question',
        )
        notifications_before_admin_reply = queue_notification.call_count

        url = reverse('shop:whatsapp_webhook')
        payload = {
            'From': 'whatsapp:+2349130273282',
            'Body': 'We are looking into this for you.',
        }
        signature = self._twilio_signature(
            f'http://testserver{url}',
            payload,
            'webhook-secret',
        )

        response = self.client.post(
            url,
            payload,
            HTTP_X_TWILIO_SIGNATURE=signature,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/xml')
        self.assertEqual(response.content, b'<Response></Response>')
        reply = ChatMessage.objects.get(
            chat=latest_chat,
            sender_type='admin',
            text='We are looking into this for you.',
        )
        self.assertIsNotNone(reply)
        self.assertEqual(queue_notification.call_count, notifications_before_admin_reply)

        self.client.force_login(latest_chat.user)
        chat_response = self.client.get(
            reverse('shop:fetch_support_messages'),
            {'after_id': latest_customer_message.id},
        )
        self.assertEqual(chat_response.status_code, 200)
        self.assertEqual(chat_response.json()['messages'][0]['id'], reply.id)
        self.assertEqual(chat_response.json()['messages'][0]['text'], reply.text)

    @override_settings(SECURE_SSL_REDIRECT=False)
    @patch.dict(os.environ, {
        'TWILIO_AUTH_TOKEN': 'webhook-secret',
        'TWILIO_WHATSAPP_ADMIN_NUMBER': '+2349130273282',
    }, clear=True)
    def test_twilio_webhook_rejects_messages_from_non_admin_number(self):
        url = reverse('shop:whatsapp_webhook')
        payload = {'From': 'whatsapp:+1234567890', 'Body': 'Spoofed reply'}
        signature = self._twilio_signature(
            f'http://testserver{url}',
            payload,
            'webhook-secret',
        )

        response = self.client.post(url, payload, HTTP_X_TWILIO_SIGNATURE=signature)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ChatMessage.objects.filter(sender_type='admin').exists())

    @override_settings(SECURE_SSL_REDIRECT=False)
    @patch.dict(os.environ, {'TWILIO_AUTH_TOKEN': 'webhook-secret'}, clear=True)
    def test_twilio_webhook_rejects_invalid_signature(self):
        response = self.client.post(
            reverse('shop:whatsapp_webhook'),
            {'From': 'whatsapp:+2349130273282', 'Body': 'Unverified reply'},
            HTTP_X_TWILIO_SIGNATURE='invalid',
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(ChatMessage.objects.filter(sender_type='admin').exists())

    @override_settings(SECURE_SSL_REDIRECT=False)
    @patch.dict(os.environ, {
        'TWILIO_AUTH_TOKEN': 'webhook-secret',
        'TWILIO_WHATSAPP_ADMIN_NUMBER': '+2349130273282',
    }, clear=True)
    def test_twilio_reply_sid_routes_reply_to_matching_customer_chat(self):
        first_chat = SupportChat.objects.create(user=User.objects.create_user(
            username='firstcustomer',
            email='first@example.com',
        ))
        first_message = ChatMessage.objects.create(
            chat=first_chat,
            sender_type='user',
            text='First customer question',
            twilio_message_sid='SM' + '1' * 32,
        )
        second_chat = SupportChat.objects.create(user=User.objects.create_user(
            username='secondcustomer',
            email='second@example.com',
        ))
        ChatMessage.objects.create(
            chat=second_chat,
            sender_type='user',
            text='Second customer question',
            twilio_message_sid='SM' + '2' * 32,
        )

        url = reverse('shop:whatsapp_webhook')
        payload = {
            'From': 'whatsapp:+2349130273282',
            'Body': 'Reply to the first customer.',
            'OriginalRepliedMessageSid': first_message.twilio_message_sid,
        }
        signature = self._twilio_signature(
            f'http://testserver{url}',
            payload,
            'webhook-secret',
        )

        response = self.client.post(url, payload, HTTP_X_TWILIO_SIGNATURE=signature)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            first_chat.messages.filter(
                sender_type='admin',
                text='Reply to the first customer.',
            ).exists()
        )
        self.assertFalse(
            second_chat.messages.filter(
                sender_type='admin',
                text='Reply to the first customer.',
            ).exists()
        )

    @override_settings(SECURE_SSL_REDIRECT=False, PUBLIC_BASE_URL='https://hoxobil.store')
    @patch.dict(os.environ, {
        'TWILIO_AUTH_TOKEN': 'webhook-secret',
        'TWILIO_WHATSAPP_ADMIN_NUMBER': '+2349130273282',
    }, clear=True)
    @patch('shop.whatsapp.queue_whatsapp_notification')
    @patch('shop.utils.send_hoxobil_email')
    def test_whatsapp_reply_teaches_bot_and_emails_customer(
        self,
        send_email,
        queue_notification,
    ):
        user = User.objects.create_user(username='learncustomer', email='learn@example.com')
        chat = SupportChat.objects.create(user=user)
        question = ChatMessage.objects.create(
            chat=chat,
            sender_type='user',
            text='Does the canvas tote have an inside pocket?',
            twilio_message_sid='SM' + '4' * 32,
        )
        unknown = UnknownQuestion.objects.create(
            user=user,
            message='does the canvas tote have an inside pocket',
            session_step='awaiting_intent',
        )
        url = reverse('shop:whatsapp_webhook')
        payload = {
            'From': 'whatsapp:+2349130273282',
            'Body': 'Yes, the canvas tote has one interior pocket.',
            'OriginalRepliedMessageSid': question.twilio_message_sid,
        }
        signature = self._twilio_signature(
            f'http://testserver{url}',
            payload,
            'webhook-secret',
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload, HTTP_X_TWILIO_SIGNATURE=signature)

        self.assertEqual(response.status_code, 200)
        knowledge = BotKnowledge.objects.get(category='learned')
        self.assertEqual(knowledge.keywords, unknown.message)
        self.assertEqual(knowledge.answer, payload['Body'])
        unknown.refresh_from_db()
        self.assertEqual(unknown.status, 'answered')
        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.args[0], user.email)
        self.assertIn('https://hoxobil.store/support/chat/', send_email.call_args.args[2])


@override_settings(SECURE_SSL_REDIRECT=False)
class HoxobotSupportWorkflowTests(TestCase):
    def setUp(self):
        self.customer = User.objects.create_user(
            username='workflowcustomer',
            email='workflow@example.com',
            password='SecurePassword123!',
        )
        self.chat = SupportChat.objects.create(user=self.customer)
        self.client.force_login(self.customer)

    def test_knowledge_answer_bypasses_active_design_step(self):
        BotKnowledge.objects.create(
            title='Shipping schedule',
            category='shipping',
            keywords='shipping schedule, delivery timeline',
            answer='Orders ship after production is complete.',
        )
        context = {
            'current_step': 'awaiting_placement',
            'garment': 'Hoodie',
            'color': 'Black',
            'size': 'L',
        }

        from shop.ai_bot import bot

        answer, updated_context, upload = bot.get_response(
            'What is the shipping schedule?',
            context=context,
            user=self.customer,
        )

        self.assertEqual(answer, 'Orders ship after production is complete.')
        self.assertEqual(updated_context['current_step'], 'awaiting_placement')
        self.assertFalse(upload)
        self.assertEqual(BotKnowledge.objects.get(category='shipping').times_used, 1)

    @patch('shop.whatsapp.queue_whatsapp_notification')
    @patch('shop.services.search_web_answer', return_value=None)
    def test_unanswered_question_escalates_and_alerts_admin(
        self,
        search_web_answer,
        queue_notification,
    ):
        session = self.client.session
        session['hoxo_chat_context'] = {
            'current_step': 'awaiting_placement',
            'garment': 'Hoodie',
            'color': 'Black',
            'size': 'L',
        }
        session.save()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse('shop:send_support_message'),
                {'message': 'What is the warranty on this material?'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('Connecting you with an agent', response.json()['auto_reply'])
        self.chat.refresh_from_db()
        self.assertTrue(self.chat.human_escalated)
        self.assertTrue(
            UnknownQuestion.objects.filter(
                user=self.customer,
                status='pending',
                message='what is the warranty on this material',
            ).exists()
        )
        self.assertTrue(any(
            'Human support requested' in call.args[2]
            for call in queue_notification.call_args_list
        ))
        search_web_answer.assert_called_once_with('what is the warranty on this material?')

    @patch('shop.whatsapp.queue_whatsapp_notification')
    def test_greetings_and_menu_selections_do_not_send_whatsapp_alerts(self, queue_notification):
        messages = ['hi', 'hello', 'whats up', "what's up", 'hey', 'A', 'B', 'C', 'D', 'E']

        for message in messages:
            session = self.client.session
            session['hoxo_chat_context'] = {'current_step': 'awaiting_intent'}
            session.save()
            with self.subTest(message=message):
                response = self.client.post(
                    reverse('shop:send_support_message'),
                    {'message': message},
                )
                self.assertEqual(response.status_code, 200)

        self.chat.refresh_from_db()
        self.assertFalse(self.chat.human_escalated)
        queue_notification.assert_not_called()
        self.assertFalse(UnknownQuestion.objects.filter(user=self.customer).exists())

    @patch('shop.whatsapp.queue_whatsapp_notification')
    @patch('shop.services.search_web_answer', return_value='Here is what I found online: The warranty lasts one year.')
    def test_web_search_answer_prevents_knowledge_gap_escalation(
        self,
        search_web_answer,
        queue_notification,
    ):
        response = self.client.post(
            reverse('shop:send_support_message'),
            {'message': 'What is the warranty on this material?'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('The warranty lasts one year', response.json()['auto_reply'])
        self.chat.refresh_from_db()
        self.assertFalse(self.chat.human_escalated)
        self.assertFalse(UnknownQuestion.objects.filter(user=self.customer).exists())
        search_web_answer.assert_called_once_with('what is the warranty on this material?')
        self.assertFalse(any(
            'Human support requested' in call.args[2]
            for call in queue_notification.call_args_list
        ))

    @override_settings(SERPER_API_KEY='serper-test-key')
    @patch('shop.services.requests.post')
    def test_web_search_returns_relevant_answer_and_ignores_unrelated_results(self, post):
        from shop.services import search_web_answer

        response = MagicMock()
        response.json.return_value = {
            'organic': [{
                'title': 'Cotton fabric warranty',
                'snippet': 'The cotton fabric warranty lasts one year.',
                'link': 'https://example.com/warranty',
            }],
        }
        post.return_value = response

        answer = search_web_answer('What is the warranty on cotton fabric?')

        self.assertIn('warranty lasts one year', answer)
        self.assertIn('https://example.com/warranty', answer)
        self.assertEqual(post.call_args.kwargs['timeout'], (3, 6))

        response.json.return_value = {
            'organic': [{
                'title': 'Weather today',
                'snippet': 'Expect rain in the afternoon.',
                'link': 'https://example.com/weather',
            }],
        }
        self.assertIsNone(search_web_answer('What is the warranty on cotton fabric?'))

    @patch('shop.whatsapp.queue_whatsapp_notification')
    def test_cart_menu_choice_returns_structured_cart_card(self, queue_notification):
        response = self.client.post(
            reverse('shop:send_support_message'),
            {'message': 'B'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tool_result']['name'], 'get_cart_contents')
        self.assertEqual(response.json()['tool_result']['data']['items'], [])

    @patch('shop.utils.send_hoxobil_email')
    @patch('shop.whatsapp.queue_whatsapp_notification')
    @override_settings(PUBLIC_BASE_URL='https://hoxobil.store')
    def test_admin_chat_dashboard_sends_file_reply_and_teaches_bot(
        self,
        queue_notification,
        send_email,
    ):
        customer_question = 'what is the warranty on this material'
        ChatMessage.objects.create(
            chat=self.chat,
            sender_type='user',
            text=customer_question,
        )
        unknown = UnknownQuestion.objects.create(
            user=self.customer,
            message=customer_question,
            session_step='awaiting_placement',
        )
        admin = User.objects.create_superuser(
            username='supportadmin',
            email='admin@example.com',
            password='SecurePassword123!',
        )
        self.client.force_login(admin)
        dashboard_url = reverse('admin_chat_detail', args=[self.chat.pk])
        dashboard_response = self.client.get(dashboard_url)
        self.assertEqual(dashboard_response.status_code, 200)
        self.assertContains(dashboard_response, 'Support chat')

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(
                        dashboard_url,
                        {
                            'message': 'The fabric warranty is one year.',
                            'attachment': SimpleUploadedFile(
                                'fabric-guide.pdf',
                                b'PDF test attachment',
                                content_type='application/pdf',
                            ),
                        },
                    )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['message']['attachment_name'], 'fabric-guide.pdf')
                self.assertEqual(response.json()['message']['sender_type'], 'admin')
                knowledge = BotKnowledge.objects.get(category='learned')
                self.assertEqual(knowledge.keywords, customer_question)
                self.assertEqual(knowledge.answer, 'The fabric warranty is one year.')
                unknown.refresh_from_db()
                self.assertEqual(unknown.status, 'answered')
                self.assertEqual(unknown.converted_to, knowledge)

        send_email.assert_called_once()
        self.assertEqual(send_email.call_args.args[0], self.customer.email)
        self.assertEqual(send_email.call_args.args[1], 'New reply from Hoxobil Support')
        self.assertIn('https://hoxobil.store/support/chat/', send_email.call_args.args[2])

    @patch('shop.whatsapp.queue_whatsapp_notification')
    def test_new_design_request_sends_chat_linked_whatsapp_alert(self, queue_notification):
        with self.captureOnCommitCallbacks(execute=True):
            ticket = CustomDesignTicket.objects.create(
                user=self.customer,
                garment_item='Custom hoodie',
                garment_color='Black',
                garment_size='L',
                custom_text='Create a geometric print',
                placement='Front',
            )

        queue_notification.assert_called_once()
        self.assertEqual(queue_notification.call_args.kwargs['chat_id'], self.chat.pk)
        self.assertIn('New design request submitted', queue_notification.call_args.args[2])
        self.assertIsNotNone(ticket.pk)