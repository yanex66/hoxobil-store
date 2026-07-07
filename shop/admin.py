import time
import json
from decimal import Decimal, InvalidOperation
from django.contrib import admin
from django.utils.safestring import mark_safe
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.contrib import messages

from .models import (
    Product, Category, Order, OrderItem, ProductVariant,
    VideoAd, CustomOrderRequest, SupportChat, ChatMessage, DesignSubmission,
    CustomDesignTicket, BotKnowledge, UnknownQuestion, Review,
)
from .pod_api import PodApiClient

PUBLISH_DELAY = 5


def _drop_invoice_into_chat(ticket, request=None, base_url=None):
    """
    Drops a price breakdown + inline Add-to-Cart widget into the customer's chat.
    The [ADD_TO_CART:ticketId:amount] token is detected by chat_support.html JS
    and rendered as a cart button — no separate checkout page needed.
    Marks invoice_sent = True so it can never be sent twice.
    """
    invoice_amount = getattr(ticket, 'invoice_amount', None)
    if not invoice_amount or not ticket.user or ticket.invoice_sent:
        return False

    chat, _ = SupportChat.objects.get_or_create(user=ticket.user)

    # Build a readable price breakdown if NGN breakdown fields exist
    product_ngn  = getattr(ticket, 'product_price_ngn', None)
    shipping_ngn = getattr(ticket, 'shipping_cost_ngn', None)

    if product_ngn and shipping_ngn:
        breakdown = (
            f"\n\n📋 **Price Breakdown:**\n"
            f"• Garment cost: ₦{product_ngn:,}\n"
            f"• Shipping: ₦{shipping_ngn:,}\n"
            f"• **Total: ₦{invoice_amount:,}**"
        )
    else:
        breakdown = f"\n\n**Total: ₦{invoice_amount:,}**"

    ChatMessage.objects.create(
        chat=chat,
        sender_type='admin',
        text=(
            f"💳 **Your Custom Order Invoice is Ready!**\n\n"
            f"Your custom **{ticket.garment_item}** has been priced and is ready to order."
            f"{breakdown}\n\n"
            f"👇 Tap **Add to Cart** below, then head to your cart to complete payment "
            f"together with any other items.\n\n"
            f"[ADD_TO_CART:{ticket.id}:{invoice_amount}]"
        )
    )

    ticket.status = 'Approved & Ready for Production'
    ticket.invoice_sent = True
    ticket.save(update_fields=['status', 'invoice_sent'])
    return True


# ─────────────────────────────────────────────────────────
# INLINES
# ─────────────────────────────────────────────────────────

class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ('size', 'color', 'price', 'pod_id', 'available')
    readonly_fields = ('pod_id',)


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ('created_at',)


class DesignSubmissionInline(admin.TabularInline):
    model = DesignSubmission
    extra = 0
    readonly_fields = ('submitted_at', 'updated_at')


# ─────────────────────────────────────────────────────────
# ADMIN CLASSES
# ─────────────────────────────────────────────────────────

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'price', 'get_categories', 'available', 'pod_id', 'created', 'publish_button']
    list_filter = ['available', 'categories']
    list_editable = ['price', 'available']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name', 'pod_id']
    fields = (
        'name', 'slug', 'categories', 'price', 'pod_service', 'pod_id',
        'available', 'description', 'image_url', 'image_file', 'print_areas'
    )
    inlines = [ProductVariantInline]

    def get_categories(self, obj):
        return ', '.join(obj.categories.values_list('name', flat=True))
    get_categories.short_description = 'Categories'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:pod_id>/publish/',
                self.admin_site.admin_view(self.publish_product_view),
                name='shop_product_publish',
            ),
        ]
        return custom_urls + urls

    def publish_button(self, obj):
        if not obj.pod_id:
            return '—'
        url = reverse('admin:shop_product_publish', args=[obj.pod_id])
        return format_html(
            '<a class="button" href="{}" style="'
            'background:#417690;color:#fff;padding:4px 10px;'
            'border-radius:4px;text-decoration:none;font-size:12px;">'
            'Publish</a>',
            url
        )
    publish_button.short_description = 'Printify'
    publish_button.allow_tags = True

    def publish_product_view(self, request, pod_id):
        client = PodApiClient('PFY')
        time.sleep(2)
        success = client.publish_product(pod_id)
        if success:
            self.message_user(request, f"Product {pod_id} published successfully.", messages.SUCCESS)
        else:
            self.message_user(
                request,
                f"Failed to publish product {pod_id}. Check your logs for details.",
                messages.ERROR,
            )
        return redirect('admin:shop_product_changelist')

    def publish_all_action(self, request, queryset):
        client = PodApiClient('PFY')
        success_count = 0
        fail_count = 0
        skipped_count = 0

        products = list(queryset)
        for i, product in enumerate(products):
            if not product.pod_id:
                skipped_count += 1
                continue

            success = client.publish_product(product.pod_id)
            if success:
                success_count += 1
            else:
                fail_count += 1

            if i < len(products) - 1:
                time.sleep(PUBLISH_DELAY)

        if success_count:
            self.message_user(request, f"{success_count} product(s) published successfully.", messages.SUCCESS)
        if fail_count:
            self.message_user(request, f"{fail_count} product(s) failed. Check your logs for details.", messages.ERROR)
        if skipped_count:
            self.message_user(request, f"{skipped_count} product(s) skipped — no Printify ID set.", messages.WARNING)

    publish_all_action.short_description = "Publish selected products to Printify"
    actions = [publish_all_action]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'first_name', 'last_name', 'email', 'status', 'paid', 'created']
    list_filter = ['paid', 'status', 'created']
    search_fields = ['first_name', 'last_name', 'email', 'pod_order_id']


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'product', 'product_variant', 'price', 'quantity']
    search_fields = ['product__name', 'order__id', 'product_variant__pod_id']


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ['product', 'user', 'rating', 'is_approved', 'created_at']
    list_filter = ['rating', 'is_approved', 'created_at']
    search_fields = ['product__name', 'user__username', 'user__email', 'title', 'comment']
    list_editable = ['is_approved']
    readonly_fields = ['product', 'user', 'order_item', 'rating', 'title', 'comment', 'created_at', 'updated_at']


@admin.register(VideoAd)
class VideoAdAdmin(admin.ModelAdmin):
    list_display = ('title', 'placement', 'is_active', 'created_at')
    list_filter = ('is_active', 'placement')
    search_fields = ('title',)
    list_editable = ('is_active', 'placement')


@admin.register(CustomOrderRequest)
class CustomOrderRequestAdmin(admin.ModelAdmin):
    list_display  = ['full_name', 'email', 'product_type', 'size', 'quantity', 'status', 'created']
    list_filter   = ['status', 'product_type', 'created']
    search_fields = ['full_name', 'email']
    readonly_fields = ['created']
    list_editable = ['status']


@admin.register(SupportChat)
class SupportChatAdmin(admin.ModelAdmin):
    list_display = ['user', 'created_at', 'updated_at']
    search_fields = ['user__username', 'user__email', 'user__first_name']
    inlines = [ChatMessageInline, DesignSubmissionInline]


@admin.register(DesignSubmission)
class DesignSubmissionAdmin(admin.ModelAdmin):
    list_display = ['chat', 'proof_status', 'submitted_at']
    list_filter = ['proof_status']
    readonly_fields = ['submitted_at', 'updated_at']


@admin.register(CustomDesignTicket)
class CustomDesignTicketAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'garment_item', 'user', 'status', 'garment_color',
        'garment_size', 'invoice_amount_display', 'chat_button', 'invoice_button'
    )
    list_filter = ('status', 'garment_item')
    search_fields = ('user__username', 'user__email', 'session_key', 'custom_text')

    fieldsets = (
        ('Customer Info', {
            'fields': ('user', 'session_key', 'status'),
            'description': 'Track user authentication records and current ticket lifecycle phase.'
        }),
        ('Blank Garment Specs', {
            'fields': ('garment_item', 'garment_color', 'garment_size'),
            'description': 'Base apparel inventory profiles selected by the customer.'
        }),
        ('Design Blueprint (Customer Request)', {
            'fields': ('custom_text', 'typography_style', 'placement'),
            'description': 'Custom configuration requests extracted from the automated chat conversation flow.'
        }),
        ('Printful Integration', {
            'fields': ('printful_product_id', 'fetch_price_button', 'fetch_mockup_button'),
            'description': (
                '1. Design the product on Printful dashboard. '
                '2. Paste the Printful Product ID here and save. '
                '3. Click "Fetch Price + Shipping" to auto-calculate the invoice amount. '
                '4. Click "Fetch Mockup from Printful" to pull the mockup and send it to the customer.'
            ),
        }),
        ('Design Team Action (Upload Mockup Manually)', {
            'fields': ('design_team_mockup', 'live_chat_panel'),
            'description': 'Alternative: upload a PNG/JPG proof manually. Saving pushes it into the customer\'s chat.',
            'classes': ('collapse',),
        }),
        ('Invoice & Payment', {
            'fields': ('invoice_amount', 'invoice_sent'),
            'description': (
                'Set the total price in NGN. '
                'Once the customer approves the mockup in chat, the payment link is sent automatically. '
                'You can also trigger it manually from the ticket list using the 💳 Send Invoice button.'
            ),
        }),
    )

    readonly_fields = ('live_chat_panel', 'fetch_mockup_button', 'fetch_price_button', 'invoice_sent')

    def fetch_price_button(self, obj):
        if not obj.pk:
            return mark_safe('<p style="color:#999;">Save the ticket first.</p>')
        if not obj.printful_product_id:
            return mark_safe('<p style="color:#999;">Enter a Printful Product ID above and save first.</p>')
        url = reverse('admin:shop_ticket_fetch_price', args=[obj.id])
        extra = ''
        if obj.invoice_amount:
            extra = f'<p style="margin-top:6px;font-size:12px;color:#555;">Current invoice: <strong>₦{obj.invoice_amount:,}</strong></p>'
        return format_html(
            '<a class="button" href="{}" style="'
            'background:#27ae60;color:#fff;padding:6px 14px;'
            'border-radius:4px;text-decoration:none;font-size:13px;font-weight:bold;">'
            '💰 Fetch Price + Shipping from Printful</a>'
            '{}',
            url, mark_safe(extra)
        )
    fetch_price_button.short_description = 'Price & Shipping'

    def fetch_mockup_button(self, obj):
        if not obj.pk:
            return mark_safe('<p style="color:#999;">Save the ticket first, then fetch the mockup.</p>')
        if not obj.printful_product_id:
            return mark_safe('<p style="color:#999;">Enter a Printful Product ID above and save first.</p>')
        url = reverse('admin:shop_ticket_fetch_mockup', args=[obj.id])
        return format_html(
            '<a class="button" href="{}" style="'
            'background:#2980b9;color:#fff;padding:6px 14px;'
            'border-radius:4px;text-decoration:none;font-size:13px;font-weight:bold;">'
            '🖼 Fetch Mockup from Printful</a>',
            url
        )
    fetch_mockup_button.short_description = 'Printful Mockup'

    def invoice_amount_display(self, obj):
        amount = getattr(obj, 'invoice_amount', None)
        if amount:
            return f"₦{amount:,}"
        return '—'
    invoice_amount_display.short_description = 'Invoice'

    def invoice_button(self, obj):
        amount = getattr(obj, 'invoice_amount', None)
        if not amount or not obj.user:
            return '—'
        url = reverse('admin:shop_ticket_send_invoice', args=[obj.id])
        return format_html(
            '<a class="button" href="{}" style="'
            'background:#c0392b;color:#fff;padding:4px 10px;'
            'border-radius:4px;text-decoration:none;font-size:12px;">'
            '💳 Send Invoice</a>',
            url
        )
    invoice_button.short_description = 'Invoice Action'

    def chat_button(self, obj):
        if not obj.user:
            return '—'
        url = reverse('admin:shop_customdesignticket_change', args=[obj.id])
        return format_html(
            '<a class="button" href="{}#chat-panel" style="'
            'background:#1a7a4a;color:#fff;padding:4px 10px;'
            'border-radius:4px;text-decoration:none;font-size:12px;">'
            '💬 Open Chat</a>',
            url
        )
    chat_button.short_description = 'Chat'

    def live_chat_panel(self, obj):
        if not obj.user:
            return format_html('<p style="color:#999;">No user linked to this ticket.</p>')

        chat = SupportChat.objects.filter(user=obj.user).first()
        if not chat:
            return format_html('<p style="color:#999;">No chat session found for this customer.</p>')

        messages_qs = chat.messages.order_by('created_at')

        bubbles_html = ''
        for msg in messages_qs:
            is_admin = msg.sender_type == 'admin'
            align = 'right' if is_admin else 'left'
            bg    = '#1a7a4a' if is_admin else '#f0f0f0'
            color = '#fff'    if is_admin else '#222'
            label = '🛠 Design Team' if is_admin else f'👤 {obj.user.first_name or obj.user.username}'
            time_str = msg.created_at.strftime('%d %b, %H:%M') if msg.created_at else ''

            image_html = ''
            if msg.image_field:
                image_html = f'<br><img src="{msg.image_field.url}" style="max-width:260px;border-radius:8px;margin-top:8px;">'

            bubbles_html += f'''
            <div style="display:flex;justify-content:flex-{align};margin-bottom:12px;">
                <div style="max-width:70%;background:{bg};color:{color};padding:10px 14px;
                            border-radius:12px;font-size:13px;line-height:1.5;">
                    <div style="font-size:11px;opacity:0.75;margin-bottom:4px;">{label} · {time_str}</div>
                    {msg.text}{image_html}
                </div>
            </div>'''

        if not bubbles_html:
            bubbles_html = '<p style="color:#999;text-align:center;">No messages yet.</p>'

        reply_url = reverse('admin:shop_ticket_admin_reply', args=[obj.id])

        return mark_safe('''
            <div id="chat-panel" style="border:1px solid #ddd;border-radius:10px;overflow:hidden;
                                        font-family:sans-serif;margin-top:8px;">
                <div style="background:#1a7a4a;color:#fff;padding:12px 16px;font-weight:bold;font-size:14px;">
                    💬 Live Chat — {username} ({email})
                </div>
                <div id="chat-messages" style="height:400px;overflow-y:auto;padding:16px;background:#fafafa;">
                    {bubbles}
                </div>
                <div style="border-top:1px solid #ddd;padding:12px;background:#fff;display:flex;gap:8px;align-items:flex-end;">
                    <textarea id="admin-reply-text" rows="2"
                        placeholder="Type your reply to the customer..."
                        style="flex:1;padding:10px;border:1px solid #ccc;border-radius:6px;
                               font-size:13px;resize:vertical;font-family:sans-serif;"></textarea>
                    <button type="button" onclick="sendAdminReply('{reply_url}')"
                        style="background:#1a7a4a;color:#fff;border:none;padding:10px 18px;
                               border-radius:6px;cursor:pointer;font-size:13px;font-weight:bold;">
                        Send ➤
                    </button>
                </div>
            </div>

            <script>
            function sendAdminReply(url) {{
                var text = document.getElementById('admin-reply-text').value.trim();
                if (!text) return;
                fetch(url, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-CSRFToken': document.cookie.match(/csrftoken=([^;]+)/)[1]
                    }},
                    body: 'message=' + encodeURIComponent(text)
                }})
                .then(r => r.json())
                .then(data => {{
                    if (data.status === 'ok') {{
                        document.getElementById('admin-reply-text').value = '';
                        var feed = document.getElementById('chat-messages');
                        var now = new Date().toLocaleString('en-GB', {{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}});
                        feed.innerHTML += `
                            <div style="display:flex;justify-content:flex-end;margin-bottom:12px;">
                                <div style="max-width:70%;background:#1a7a4a;color:#fff;padding:10px 14px;
                                            border-radius:12px;font-size:13px;line-height:1.5;">
                                    <div style="font-size:11px;opacity:0.75;margin-bottom:4px;">🛠 Design Team · ${{now}}</div>
                                    ${{text}}
                                </div>
                            </div>`;
                        feed.scrollTop = feed.scrollHeight;
                    }}
                }})
                .catch(err => alert('Send failed: ' + err));
            }}
            window.addEventListener('load', function() {{
                var feed = document.getElementById('chat-messages');
                if (feed) feed.scrollTop = feed.scrollHeight;
            }});
            </script>
        '''.format(
            username=obj.user.get_full_name() or obj.user.username,
            email=obj.user.email,
            bubbles=bubbles_html,
            reply_url=reply_url,
        ))

    live_chat_panel.short_description = 'Customer Chat'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:ticket_id>/reply/',
                self.admin_site.admin_view(self.admin_reply_view),
                name='shop_ticket_admin_reply',
            ),
            path(
                '<int:ticket_id>/send-invoice/',
                self.admin_site.admin_view(self.send_invoice_view),
                name='shop_ticket_send_invoice',
            ),
            path(
                '<int:ticket_id>/fetch-mockup/',
                self.admin_site.admin_view(self.fetch_mockup_from_printful_view),
                name='shop_ticket_fetch_mockup',
            ),
            path(
                '<int:ticket_id>/fetch-price/',
                self.admin_site.admin_view(self.fetch_price_and_shipping_view),
                name='shop_ticket_fetch_price',
            ),
        ]
        return custom_urls + urls

    def fetch_price_and_shipping_view(self, request, ticket_id):
        import requests as _req
        from django.http import HttpResponseRedirect
        from django.conf import settings as _settings

        ticket = CustomDesignTicket.objects.get(id=ticket_id)
        redirect_url = reverse('admin:shop_customdesignticket_change', args=[ticket_id])

        if not ticket.printful_product_id:
            self.message_user(request, "Set a Printful Product ID first.", level='error')
            return HttpResponseRedirect(redirect_url)

        headers = {
            'Authorization': f'Bearer {_settings.PRINTFUL_ACCESS_TOKEN}',
            'X-PF-Store-Id': str(_settings.PRINTFUL_STORE_ID),
            'Content-Type': 'application/json',
        }

        # ── 1. Fetch sync product to get variants + retail price ──────────────
        product_price_usd  = Decimal('0')
        sync_variant_id    = None   # used for shipping payload
        external_variant_id = None  # the catalog variant_id field

        try:
            api_url = f'https://api.printful.com/store/products/{ticket.printful_product_id}'
            resp = _req.get(api_url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            sync_product = data.get('result', {})
            variants = sync_product.get('sync_variants', [])

            if not variants:
                self.message_user(request, "No variants found for this Printful product.", level='error')
                return HttpResponseRedirect(redirect_url)

            # Try to match by size, fall back to first variant
            target_size = (ticket.garment_size or '').upper()
            matched_variant = None
            for v in variants:
                if target_size and target_size in (v.get('name') or '').upper():
                    matched_variant = v
                    break
            if not matched_variant:
                matched_variant = variants[0]

            # sync variant ID (the store's internal ID)
            sync_variant_id = matched_variant.get('id')

            # external_variant_id is the catalog variant ID — used by shipping API
            # It lives at matched_variant['main_category_id'] → no, it's variant_id
            external_variant_id = matched_variant.get('variant_id')

            retail_price = (
                matched_variant.get('retail_price')
                or sync_product.get('sync_product', {}).get('retail_price')
            )
            if retail_price:
                product_price_usd = Decimal(str(retail_price))

        except Exception as e:
            self.message_user(request, f"Failed to fetch product price from Printful: {e}", level='error')
            return HttpResponseRedirect(redirect_url)

        # ── 2. Fetch shipping estimate ────────────────────────────────────────
        shipping_cost_usd = Decimal('0')
        shipping_rate_name = ''

        customer_order = None
        if ticket.user:
            customer_order = (
                Order.objects.filter(user=ticket.user)
                .exclude(address='')
                .order_by('-created')
                .first()
            )

        if customer_order:
            recipient_country = customer_order.country or 'NG'
            recipient_zip     = customer_order.postal_code or '100001'
            recipient_state   = customer_order.state or ''
            recipient_city    = customer_order.city or 'Lagos'
            recipient_address = customer_order.address or 'N/A'
        else:
            recipient_country = 'NG'
            recipient_zip     = '100001'
            recipient_state   = ''
            recipient_city    = 'Lagos'
            recipient_address = 'N/A'

        # Printful /shipping/rates accepts either:
        #   { "variant_id": <catalog_variant_id>, "quantity": 1 }   ← preferred
        #   { "sync_variant_id": <store_variant_id>, "quantity": 1 } ← fallback
        # We try catalog variant_id first, then fall back to sync_variant_id
        shipping_item = None
        if external_variant_id:
            shipping_item = {"variant_id": external_variant_id, "quantity": 1}
        elif sync_variant_id:
            shipping_item = {"sync_variant_id": sync_variant_id, "quantity": 1}

        if shipping_item:
            try:
                shipping_payload = {
                    "recipient": {
                        "address1":     recipient_address,
                        "city":         recipient_city,
                        "country_code": recipient_country,
                        "zip":          recipient_zip,
                        "state_code":   recipient_state,
                    },
                    "items": [shipping_item]
                }
                ship_resp = _req.post(
                    'https://api.printful.com/shipping/rates',
                    headers=headers,
                    json=shipping_payload,
                    timeout=15,
                )
                ship_data = ship_resp.json()

                if ship_resp.status_code == 200 and ship_data.get('code') == 200:
                    rates = ship_data.get('result', [])
                    if rates:
                        cheapest = min(rates, key=lambda r: float(r.get('rate', 0)))
                        shipping_cost_usd = Decimal(str(cheapest.get('rate', '0')))
                        shipping_rate_name = cheapest.get('name', '')
                else:
                    err = ship_data.get('error', {})
                    err_msg = err.get('message', str(err)) if isinstance(err, dict) else str(err)
                    self.message_user(
                        request,
                        f"Shipping estimate unavailable ({err_msg}). Product price fetched — shipping set to ₦0.",
                        level='warning'
                    )
            except Exception as e:
                self.message_user(
                    request,
                    f"Shipping API error: {e}. Product price fetched — shipping set to ₦0.",
                    level='warning'
                )
        else:
            self.message_user(
                request,
                "Could not determine variant ID for shipping. Product price fetched — shipping set to ₦0.",
                level='warning'
            )

        # ── 3. Convert USD → NGN ──────────────────────────────────────────────
        try:
            rates_map = getattr(_settings, 'CASH_EXCHANGE_BACKEND', {}).get('USD', {})
            ngn_rate  = Decimal(str(rates_map.get('NGN', 1500)))
        except Exception:
            ngn_rate = Decimal('1500')

        product_ngn  = (product_price_usd * ngn_rate).quantize(Decimal('1'))
        shipping_ngn = (shipping_cost_usd * ngn_rate).quantize(Decimal('1'))
        total_ngn    = product_ngn + shipping_ngn

        # ── 4. Save to ticket ─────────────────────────────────────────────────
        ticket.invoice_amount = total_ngn
        if hasattr(ticket, 'product_price_ngn'):
            ticket.product_price_ngn = product_ngn
        if hasattr(ticket, 'shipping_cost_ngn'):
            ticket.shipping_cost_ngn = shipping_ngn

        save_fields = ['invoice_amount']
        if hasattr(ticket, 'product_price_ngn'):
            save_fields.append('product_price_ngn')
        if hasattr(ticket, 'shipping_cost_ngn'):
            save_fields.append('shipping_cost_ngn')
        ticket.save(update_fields=save_fields)

        # ── 5. Show admin breakdown ───────────────────────────────────────────
        parts = [f"Product: ${product_price_usd} (₦{product_ngn:,})"]
        if shipping_cost_usd:
            parts.append(f"Shipping ({shipping_rate_name}): ${shipping_cost_usd} (₦{shipping_ngn:,})")
        else:
            parts.append("Shipping: not available (₦0)")
        parts.append(f"Rate: ₦{ngn_rate:,}/USD → Invoice: ₦{total_ngn:,}")
        if customer_order:
            parts.append(f"Ship-to: {recipient_city}, {recipient_country}")
        else:
            parts.append("No customer order on file — defaulted to Lagos, NG")

        self.message_user(request, "✅ " + " · ".join(parts), level='success')
        return HttpResponseRedirect(redirect_url)

    def fetch_mockup_from_printful_view(self, request, ticket_id):
        import requests as _req
        from django.core.files.base import ContentFile
        from django.http import HttpResponseRedirect

        ticket = CustomDesignTicket.objects.get(id=ticket_id)

        if not ticket.printful_product_id:
            self.message_user(request, "Set a Printful Product ID on the ticket first.", level='error')
            return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

        try:
            from django.conf import settings as _settings
            headers = {
                'Authorization': f'Bearer {_settings.PRINTFUL_ACCESS_TOKEN}',
                'X-PF-Store-Id': str(_settings.PRINTFUL_STORE_ID),
            }
            api_url = f'https://api.printful.com/store/products/{ticket.printful_product_id}'
            resp = _req.get(api_url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            sync_product = data.get('result', {})
            mockup_url = None
            mockup_url = sync_product.get('sync_product', {}).get('thumbnail_url')

            if not mockup_url:
                variants = sync_product.get('sync_variants', [])
                for variant in variants:
                    for f in variant.get('files', []):
                        if f.get('type') == 'preview':
                            mockup_url = f.get('preview_url') or f.get('url')
                            break
                        elif f.get('type') == 'default':
                            mockup_url = f.get('preview_url') or f.get('url')
                    if mockup_url:
                        break

            if not mockup_url:
                self.message_user(
                    request,
                    "Printful returned no mockup image for this product. Make sure the product has a mockup generated.",
                    level='error'
                )
                return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

            if not ticket.invoice_amount:
                try:
                    retail_price = sync_product.get('sync_product', {}).get('retail_price')
                    if not retail_price and variants:
                        retail_price = variants[0].get('retail_price')
                    if retail_price:
                        from decimal import Decimal as _Decimal
                        ticket.invoice_amount = _Decimal(str(retail_price))
                except Exception:
                    pass

            img_resp = _req.get(mockup_url, timeout=20)
            img_resp.raise_for_status()

            content_type = img_resp.headers.get('Content-Type', 'image/png')
            ext = 'jpg' if 'jpeg' in content_type else 'png'
            filename = f'printful_mockup_{ticket.id}.{ext}'

            ticket.design_team_mockup.save(filename, ContentFile(img_resp.content), save=False)
            ticket.status = 'Sent to Customer for Approval'
            ticket.save(update_fields=['design_team_mockup', 'status', 'invoice_amount'])

            if ticket.user:
                chat, _ = SupportChat.objects.get_or_create(user=ticket.user)
                parts = [p for p in [ticket.garment_color, ticket.garment_size, ticket.placement] if p]
                specs_line = f"**Specs:** {' · '.join(parts)}\n\n" if parts else ''
                msg = ChatMessage.objects.create(
                    chat=chat,
                    sender_type='admin',
                    text=(
                        f"🎨 **Your Custom Mockup Proof is Ready!**\n\n"
                        f"Our design team has built your custom **{ticket.garment_item}** on Printful. "
                        f"Here's your mockup proof — take a close look!\n\n"
                        f"{specs_line}"
                        f"👉 Reply with **'Approve'** to confirm and receive your payment link!\n"
                        f"👉 Or type any changes you'd like adjusted."
                    )
                )
                msg.image_field = ticket.design_team_mockup
                msg.save()

            self.message_user(
                request,
                "Mockup fetched from Printful and sent to customer's chat successfully.",
                level='success'
            )

        except Exception as e:
            self.message_user(request, f"Failed to fetch mockup from Printful: {e}", level='error')

        return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

    def send_invoice_view(self, request, ticket_id):
        from django.http import HttpResponseRedirect
        ticket = CustomDesignTicket.objects.get(id=ticket_id)
        invoice_amount = getattr(ticket, 'invoice_amount', None)

        if not invoice_amount:
            self.message_user(request, "Set an invoice amount on the ticket first.", level='error')
            return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

        if not ticket.user:
            self.message_user(request, "No user linked to this ticket.", level='error')
            return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

        if ticket.invoice_sent:
            self.message_user(request, "Invoice already sent. Check the customer's chat.", level='warning')
            return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

        _drop_invoice_into_chat(ticket, request)
        self.message_user(
            request,
            f"Invoice of ₦{invoice_amount:,} sent to {ticket.user.email} in chat.",
            level='success'
        )
        return HttpResponseRedirect(reverse('admin:shop_customdesignticket_change', args=[ticket_id]))

    def admin_reply_view(self, request, ticket_id):
        from django.http import JsonResponse as _JsonResponse

        if request.method != 'POST':
            return _JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

        text = request.POST.get('message', '').strip()
        if not text:
            return _JsonResponse({'status': 'error', 'message': 'Empty message'}, status=400)

        try:
            ticket = CustomDesignTicket.objects.get(id=ticket_id)
        except CustomDesignTicket.DoesNotExist:
            return _JsonResponse({'status': 'error', 'message': 'Ticket not found'}, status=404)

        if not ticket.user:
            return _JsonResponse({'status': 'error', 'message': 'No user on ticket'}, status=400)

        chat, _ = SupportChat.objects.get_or_create(user=ticket.user)
        ChatMessage.objects.create(chat=chat, sender_type='admin', text=text)
        return _JsonResponse({'status': 'ok'})

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        if 'design_team_mockup' in form.changed_data and obj.design_team_mockup:
            chat = None
            if obj.user:
                chat = SupportChat.objects.filter(user=obj.user).first()

            if chat:
                obj.status = 'Sent to Customer for Approval'
                obj.save(update_fields=['status'])

                notification_text = (
                    "🎨 **Your Custom Mockup Proof is Ready!**\n\n"
                    "Our design team has reviewed your asset specifications and cooked up your layout draft. "
                    "Take a close look at the layout mockup below.\n\n"
                    "👉 Reply with **'Approve'** to send it directly to production!\n"
                    "👉 Or type any tweaks or positioning changes you want adjusted."
                )

                msg = ChatMessage.objects.create(
                    chat=chat,
                    sender_type='admin',
                    text=notification_text
                )
                msg.image_field = obj.design_team_mockup
                msg.save()


# ─────────────────────────────────────────────────────────
# BOT KNOWLEDGE BASE ADMIN
# ─────────────────────────────────────────────────────────

@admin.register(BotKnowledge)
class BotKnowledgeAdmin(admin.ModelAdmin):
    list_display = ('keywords', 'answer_preview', 'times_used', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('keywords', 'answer')
    readonly_fields = ('times_used', 'created_at')
    list_editable = ('is_active',)

    fieldsets = (
        ('Trigger Keywords', {
            'fields': ('keywords',),
            'description': (
                'Enter comma-separated keywords that should trigger this answer. '
                'Example: "delivery, shipping, how long, when will" — if any of these appear '
                'in a customer message, the bot will reply with the answer below.'
            ),
        }),
        ('Bot Answer', {
            'fields': ('answer',),
            'description': 'What the bot will say when a matching keyword is detected. Write in the bot\'s voice.',
        }),
        ('Status', {
            'fields': ('is_active', 'times_used', 'created_at'),
        }),
    )

    def answer_preview(self, obj):
        return obj.answer[:80] + '...' if len(obj.answer) > 80 else obj.answer
    answer_preview.short_description = 'Answer Preview'


@admin.register(UnknownQuestion)
class UnknownQuestionAdmin(admin.ModelAdmin):
    list_display = ('message_preview', 'session_step', 'status', 'asked_at', 'teach_button')
    list_filter = ('status', 'session_step')
    search_fields = ('message',)
    readonly_fields = ('message', 'session_step', 'asked_at', 'user')

    fieldsets = (
        ('Customer Message', {
            'fields': ('user', 'message', 'session_step', 'asked_at', 'status'),
            'description': (
                'This message was sent by a customer but the bot had no answer for it. '
                'Click "🧠 Teach Bot" to convert it into a trained response.'
            ),
        }),
    )

    def message_preview(self, obj):
        return obj.message[:80] + '...' if len(obj.message) > 80 else obj.message
    message_preview.short_description = 'Customer Message'

    def teach_button(self, obj):
        url = reverse('admin:shop_unknownquestion_teach', args=[obj.id])
        return format_html(
            '<a class="button" href="{}" style="'
            'background:#8e44ad;color:#fff;padding:4px 10px;'
            'border-radius:4px;text-decoration:none;font-size:12px;">'
            '🧠 Teach Bot</a>',
            url
        )
    teach_button.short_description = 'Action'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:question_id>/teach/',
                self.admin_site.admin_view(self.teach_bot_view),
                name='shop_unknownquestion_teach',
            ),
        ]
        return custom_urls + urls

    def teach_bot_view(self, request, question_id):
        from django.http import HttpResponseRedirect

        try:
            question = UnknownQuestion.objects.get(id=question_id)
        except UnknownQuestion.DoesNotExist:
            self.message_user(request, "Question not found.", level='error')
            return HttpResponseRedirect(reverse('admin:shop_unknownquestion_changelist'))

        if request.method == 'POST':
            keywords = request.POST.get('keywords', '').strip()
            answer = request.POST.get('answer', '').strip()
            if keywords and answer:
                BotKnowledge.objects.create(keywords=keywords, answer=answer)
                question.status = 'resolved'
                question.save(update_fields=['status'])
                self.message_user(
                    request,
                    "✅ Bot taught successfully! It will now answer similar questions automatically.",
                    level='success'
                )
                return HttpResponseRedirect(reverse('admin:shop_unknownquestion_changelist'))
            else:
                self.message_user(request, "Both keywords and answer are required.", level='error')

        # Suggest keywords from the message itself
        suggested_keywords = ', '.join(
            w for w in question.message.lower().split()
            if len(w) > 3 and w not in {'what', 'when', 'where', 'does', 'will', 'your', 'have', 'this', 'that'}
        )[:200]

        context = {
            **self.admin_site.each_context(request),
            'question': question,
            'suggested_keywords': suggested_keywords,
            'title': 'Teach the Bot',
            'opts': self.model._meta,
        }
        return render(request, 'admin/shop/teach_bot.html', context)