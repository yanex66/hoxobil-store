import re
import json
import logging
import requests
from decimal import Decimal
from decouple import config
from .models import Product
from .utils import get_converted_money

logger = logging.getLogger(__name__)

# Symbol map for chat display. Falls back to "<CODE> " (e.g. "SEK ") for
# anything not listed here, so an unsupported currency never crashes the bot.
CURRENCY_SYMBOLS = {
    'NGN': '₦',
    'USD': '$',
    'EUR': '€',
    'GBP': '£',
    'CAD': 'C$',
    'AUD': 'A$',
    'JPY': '¥',
}

# Zero-decimal currencies (no cents/kobo shown in chat).
ZERO_DECIMAL_CURRENCIES = {'JPY'}


class HoxobilChatbot:
    """
    HOXO — Contextual Option State Machine with Upload System Flags.
    Dynamically generates and parses placement options based on raw database records,
    with an ironclad structural fallback layer for custom headwear (caps/beanies).
    """

    def __init__(self):
        self.api_key = config("SERPER_API_KEY", default="")

        # Base prices are stored in NGN (the store's base currency) as plain
        # Decimals. Display strings are built on the fly in the visitor's
        # active currency via self._price_str() / self._pricing_menu().
        # NOTE: this replaces the old hardcoded `self.pricing` dict, which
        # was never referenced anywhere else in the file.
        self.base_prices_ngn = {
            'tee':        Decimal('8500'),
            'tee_print':  Decimal('2500'),   # extra print cost add-on for tee
            'hoodie':     Decimal('14500'),
            'sweatshirt': Decimal('12000'),
            'cap':        Decimal('6000'),
        }

        self._complaint_patterns = [
            r'\b(haven.t|havent|not|didnt|didn.t)\b.{0,30}\b(custom|design|start|begin|do|made|done)\b',
            r'\b(nothing|nothing happened|not working|broken|doesn.t work|not responding)\b',
            r'\b(customize|customis|personali)\b',
            r'\b(what.s happening|what is happening|why|confused|lost)\b',
            r'\b(cloth|shirt|tee|hoodie|sweatshirt|cap|hat|beanie)\b.{0,20}\b(not|haven.t|havent)\b',
        ]

        # Terminal steps — any step at or past ticket creation.
        self.TERMINAL_STEPS = {
            'ticket_ready',
            'awaiting_instructions',
            'awaiting_upload',
            'awaiting_design_type',   # NEW
            'awaiting_font',
        }

    # ─────────────────────────────────────────────────────────
    # CURRENCY HELPERS
    # ─────────────────────────────────────────────────────────

    def _price_str(self, ngn_amount, currency_code):
        """
        Converts an NGN Decimal amount into the visitor's active currency
        and returns a formatted display string, e.g. "₦8,500" or "$5.32".
        Uses the same conversion table as the rest of the site
        (shop.utils.get_converted_money), so this always matches product
        page pricing for a given currency_code.
        """
        currency_code = (currency_code or 'NGN').upper()

        try:
            converted = get_converted_money(
                self._as_money(ngn_amount), currency_code
            )
            amount = converted.amount
        except Exception as e:
            logger.error(f"Chatbot price conversion failed: {e}")
            amount = ngn_amount
            currency_code = 'NGN'

        symbol = CURRENCY_SYMBOLS.get(currency_code, f"{currency_code} ")

        if currency_code in ZERO_DECIMAL_CURRENCIES:
            return f"{symbol}{amount:,.0f}"
        return f"{symbol}{amount:,.2f}"

    @staticmethod
    def _as_money(ngn_amount):
        from djmoney.money import Money
        return Money(ngn_amount, 'NGN')

    def _garment_menu_text(self, currency_code):
        """Builds the [A]-[D] garment selection menu in the active currency."""
        tee_price = self._price_str(self.base_prices_ngn['tee'], currency_code)
        tee_print = self._price_str(self.base_prices_ngn['tee_print'], currency_code)
        hoodie_price = self._price_str(self.base_prices_ngn['hoodie'], currency_code)
        sweatshirt_price = self._price_str(self.base_prices_ngn['sweatshirt'], currency_code)
        cap_price = self._price_str(self.base_prices_ngn['cap'], currency_code)

        return (
            f"👉 **[A] Custom Essential Tee** (Starts from {tee_price} + {tee_print} print cost)\n"
            f"👉 **[B] Heavyweight Streetwear Hoodie** (Starts from {hoodie_price})\n"
            f"👉 **[C] Custom Luxury Sweatshirt** (Starts from {sweatshirt_price})\n"
            f"👉 **[D] Streetwear Cap / Beanie** (Starts from {cap_price})"
        )

    # ─────────────────────────────────────────────────────────
    # UTILITIES
    # ─────────────────────────────────────────────────────────

    def search_web(self, query):
        clean_query = query.strip()
        if len(clean_query) <= 2:
            return "I'm locked in! Let me know what apparel blueprint option we are configuring next."

        if not self.api_key:
            return "I'm focusing on your custom gear configurations right now! Pick one of our options to continue."

        url = "https://google.serper.dev/search"
        payload = json.dumps({"q": clean_query})
        headers = {'X-API-KEY': self.api_key, 'Content-Type': 'application/json'}
        try:
            response = requests.post(url, headers=headers, data=payload, timeout=7)
            response.raise_for_status()
            data = response.json()

            raw_info = ""
            if 'answerBox' in data and 'answer' in data['answerBox']:
                raw_info = data['answerBox']['answer']
            elif 'organic' in data and len(data['organic']) > 0:
                raw_info = data['organic'][0].get('snippet', '')

            if raw_info:
                return f"HOXO Web Search Insight: {raw_info}. Let me know if you want to jump back into your options workflow!"
            return "I couldn't verify that online. Choose an active letter option to keep building your streetwear piece!"
        except Exception as e:
            logger.error(f"Serper API failure: {e}")
            return "Connection snagged! Let's stay on track with configuring your options layout."

    def _get_product_zones(self, garment_name):
        if not garment_name:
            return ['front', 'back', 'left_chest']

        product_obj = Product.objects.filter(name__icontains=garment_name).first()

        if not product_obj and garment_name.lower() in ['cap', 'hat', 'beanie', 'embroidered beanie']:
            product_obj = (
                Product.objects.filter(name__icontains='beanie').first()
                or Product.objects.filter(name__icontains='cap').first()
                or Product.objects.filter(name__icontains='hat').first()
            )

        if product_obj and product_obj.print_areas:
            return product_obj.print_areas

        if garment_name.lower() in ['cap', 'hat', 'beanie', 'embroidered beanie'] or 'beanie' in garment_name.lower():
            return ['front_cuff', 'back_cuff', 'left_side', 'right_side']

        return ['front', 'back', 'left_chest']

    def _zone_display(self, zone_key):
        return zone_key.replace('_', ' ').title()

    def _is_complaint(self, raw_msg):
        for pattern in self._complaint_patterns:
            if re.search(pattern, raw_msg, re.IGNORECASE):
                return True
        return False

    def _placement_menu(self, garment):
        zones = self._get_product_zones(garment)
        return zones, "\n".join(
            f"👉 **[{chr(65+i)}]** {self._zone_display(z)}" for i, z in enumerate(zones)
        )

    def _resume_prompt(self, context, currency_code='NGN'):
        step = context.get('current_step', 'awaiting_garment')
        garment = context.get('garment')

        if step == 'awaiting_garment' or not garment:
            return (
                "No worries! Let's kick things off. 👊\n"
                "What premium piece are we customizing today?\n\n"
                + self._garment_menu_text(currency_code)
            )
        if step == 'awaiting_size':
            return (
                f"We're customizing your **{garment}** — just need a few more details! 👕\n\n"
                "What sizing profile are we cutting this for?\n\n"
                "👉 **[A] Standard Medium (M)**\n"
                "👉 **[B] Streetwear Oversized Large (L)**\n"
                "👉 **[C] Boxy Heavyweight XL**\n"
                "👉 **[D] View Other Sizing Layouts** (XS, S, XXL, 3XL)"
            )
        if step == 'awaiting_color':
            return (
                f"Your **{garment}** (Size {context.get('size', '?')}) is pinned. 🎨\n\n"
                "Which fabric base colorway are we running with?\n\n"
                "👉 **[A] Jet Black**\n"
                "👉 **[B] Chalk White**\n"
                "👉 **[C] Classic Navy**\n"
                "👉 **[D] Charcoal Grey**"
            )
        if step == 'awaiting_placement':
            _, menu = self._placement_menu(garment)
            return (
                f"Your **{garment}** is configured. Now — where should we run your design? 📐\n\n{menu}"
            )
        if step == 'awaiting_design_type':
            return (
                "What are we printing on this? 🎨\n\n"
                "👉 **[A] Text only** — you'll type out the words\n"
                "👉 **[B] Image / graphic only** — you'll upload a picture or logo\n"
                "👉 **[C] Both** — text AND an image together"
            )
        if step == 'awaiting_upload':
            return (
                "📸 Ready for your artwork! Use the attachment icon below to upload your image or logo. "
                "Or if you're adding text too, type it here first."
            )
        if step == 'awaiting_font':
            return (
                f"We have your text: **'{context.get('custom_text_request', '')}'**\n\n"
                "How do you want the letters to look?\n\n"
                "👉 **[A] Clean & Simple** — plain, easy-to-read modern text\n"
                "👉 **[B] Bold & Heavy** — thick, strong streetwear-style lettering\n"
                "👉 **[C] Flowing Handwriting** — elegant cursive or signature style\n"
                "👉 **[D] Browse all fonts** — pick a specific font from our full list\n\n"
                "[FONT_PICKER]"
            )
        if step in ('awaiting_instructions', 'ticket_ready'):
            return (
                "Your ticket is live with the design team! 🎨\n\n"
                "They'll drop your mockup proof here shortly. "
                "Once it lands, reply **'Approve'** to confirm or type any changes you'd like."
            )
        return (
            "Your configuration is complete! 🚀 We have securely sent your specs to the design team."
        )

    # ─────────────────────────────────────────────────────────
    # MAIN RESPONSE ENGINE
    # ─────────────────────────────────────────────────────────

    def get_response(self, user_message, context=None, user=None, currency_code='NGN'):
        currency_code = (currency_code or 'NGN').upper()

        if not user_message or not user_message.strip():
            return "Hey! Choose an option or specify an apparel choice to start. 👕", context, False

        if context is None:
            context = {
                'current_step': 'awaiting_garment',
                'garment': None, 'color': None, 'size': None, 'placement': None
            }

        raw_msg = user_message.lower().strip()
        words = set(re.findall(r'\b[a-z0-9]+\b', raw_msg))
        current_step = context.get('current_step', 'awaiting_garment')

        APPROVAL_WORDS = {
            'approve', 'approved', 'yes', 'yep', 'yup', 'yeah',
            'looks good', 'perfect', 'go ahead', 'confirm', 'confirmed',
            'proceed', 'send', 'submit', 'done', 'ready', 'ok', 'okay',
            'great', 'no',
        }

        # ── 0. COMPLAINT / CONFUSION HANDLER ──────────────────────────────────────
        if current_step not in self.TERMINAL_STEPS and self._is_complaint(raw_msg):
            return self._resume_prompt(context, currency_code), context, False

        # ── TICKET_READY hard gate ─────────────────────────────────────────────────
        if current_step == 'ticket_ready':
            if words & APPROVAL_WORDS or any(a in raw_msg for a in ('looks good', 'go ahead')):
                return (
                    "🚀 **Approved and locked in!**\n\n"
                    "Your design is confirmed. Our team will now move it straight into production. "
                    "We'll ping you here with an update once it's done. Thank you! 🙌"
                ), context, False

            small_talk = {'hi', 'hello', 'hey', 'yo', 'thanks', 'thank', 'cool', 'nice', 'sup', 'bye'}
            if words & small_talk and len(words) <= 3:
                return (
                    "Hey! 👋 Your design ticket is live with the team. They'll drop your mockup here shortly.\n\n"
                    "Once it arrives, reply **'Approve'** to confirm or type any changes you want made."
                ), context, False

            context['tweak_request'] = user_message
            return (
                "🎨 **Design Team Notified!**\n\n"
                "I've passed your revision request to our designers — they'll update your mockup "
                "and drop the revised proof back here shortly."
            ), context, False

        # ── 1. GLOBAL COMMAND MATCHES ──────────────────────────────────────────────
        if words & {'hi', 'hello', 'hey', 'yo', 'start', 'restart', 'menu'}:
            if context.get('garment'):
                _, menu = self._placement_menu(context['garment'])
                if context.get('size') and context.get('color'):
                    context['current_step'] = 'awaiting_placement'
                    reply = (
                        f"Hey there! 👋 Your pinned custom **{context['garment']}** is still locked in.\n"
                        f"• Sizing fit: **Size {context['size']}**\n"
                        f"• Fabric Colorway: **{context['color']}**\n\n"
                        f"Where are we running your design layout placement?\n\n{menu}"
                    )
                elif context.get('size'):
                    context['current_step'] = 'awaiting_color'
                    reply = (
                        f"Hey there! 👋 Keeping your pinned **{context['garment']}** (Size {context['size']}).\n\n"
                        "Which fabric premium base colorway are we running with?\n\n"
                        "👉 **[A] Jet Black**\n👉 **[B] Chalk White**\n👉 **[C] Classic Navy**\n👉 **[D] Charcoal Grey**"
                    )
                else:
                    context['current_step'] = 'awaiting_size'
                    reply = (
                        f"Hey there! 👋 Keeping your pinned **{context['garment']}** selection.\n\n"
                        "What sizing profile are we cutting this for?\n\n"
                        "👉 **[A] Standard Medium (M)**\n"
                        "👉 **[B] Streetwear Oversized Large (L)**\n"
                        "👉 **[C] Boxy Heavyweight XL**\n"
                        "👉 **[D] View Other Sizing Layouts** (XS, S, XXL, 3XL)"
                    )
                return reply, context, False

            context.update({
                'current_step': 'awaiting_garment',
                'garment': None, 'color': None, 'size': None, 'placement': None
            })
            reply = (
                "Hey! 👋 Welcome to HOXOBIL. I'm HOXO, your custom design assistant.\n"
                "What premium piece are we cooking up today? Select an option letter:\n\n"
                + self._garment_menu_text(currency_code)
            )
            return reply, context, False

        if words & {'thanks', 'thank', 'cheers', 'dope', 'cool', 'awesome'}:
            return "Always a pleasure! Can't wait to see your design go live. Head over to /custom-order/ to submit your configurations! 🙌", context, False

        # --- STEP 1: GARMENT ---
        if current_step == 'awaiting_garment':
            if 'a' in words or 'tee' in raw_msg or 'tshirt' in raw_msg or 't-shirt' in raw_msg:
                context.update({'garment': 'T-Shirt', 'current_step': 'awaiting_size'})
            elif 'b' in words or 'hoodie' in raw_msg:
                context.update({'garment': 'Hoodie', 'current_step': 'awaiting_size'})
            elif 'c' in words or 'sweatshirt' in raw_msg:
                context.update({'garment': 'Sweatshirt', 'current_step': 'awaiting_size'})
            elif 'd' in words or 'cap' in raw_msg or 'hat' in raw_msg or 'beanie' in raw_msg:
                context.update({'garment': 'Cap', 'current_step': 'awaiting_placement'})
                _, menu = self._placement_menu('Cap')
                return "Premium Headwear choice. 🧢 Let's pick your layout placement:\n\n" + menu, context, False
            else:
                return (
                    "Please choose an active option. Select **A**, **B**, **C**, or **D** to start.\n\n"
                    "👉 **[A] Custom Essential Tee**\n"
                    "👉 **[B] Heavyweight Streetwear Hoodie**\n"
                    "👉 **[C] Custom Luxury Sweatshirt**\n"
                    "👉 **[D] Streetwear Cap / Beanie**"
                ), context, False

            if context.get('size') and context.get('color'):
                context['current_step'] = 'awaiting_placement'
                _, menu = self._placement_menu(context['garment'])
                reply = (
                    f"Awesome! Pinned specs loaded for your custom **{context['garment']}**.\n"
                    f"• Sizing fit: **Size {context['size']}**\n"
                    f"• Fabric Colorway: **{context['color']}**\n\n"
                    f"Where are we running your custom design layout placement?\n\n{menu}"
                )
                return reply, context, False
            elif context.get('size'):
                context['current_step'] = 'awaiting_color'
                reply = (
                    f"Sizing profile locked to **Size {context['size']}**. Which fabric premium base colorway are we running with?\n\n"
                    "👉 **[A] Jet Black**\n👉 **[B] Chalk White**\n👉 **[C] Classic Navy**\n👉 **[D] Charcoal Grey**"
                )
                return reply, context, False

            reply = (
                f"Solid fit choice! What sizing profile are we cutting this {context['garment']} for?\n\n"
                "👉 **[A] Standard Medium (M)**\n"
                "👉 **[B] Streetwear Oversized Large (L)**\n"
                "👉 **[C] Boxy Heavyweight XL**\n"
                "👉 **[D] View Other Sizing Layouts** (XS, S, XXL, 3XL)"
            )
            return reply, context, False

        # --- STEP 2: SIZING ---
        elif current_step == 'awaiting_size':
            if 'a' in words or raw_msg == 'm':
                context['size'] = 'M'
            elif 'b' in words or raw_msg == 'l':
                context['size'] = 'L'
            elif 'c' in words or raw_msg == 'xl':
                context['size'] = 'XL'
            elif 'd' in words or 'other' in raw_msg or 'more' in raw_msg:
                return "Got it. Simply type out your exact size requirement directly (e.g. XS, S, XXL, 3XL):", context, False
            else:
                size_match = words & {'xs', 'xxl', '3xl'}
                if 's' in words and len(words) == 1:
                    size_match.add('s')
                if size_match:
                    context['size'] = list(size_match)[0].upper()
                else:
                    return (
                        "Select a valid sizing option:\n\n"
                        "👉 **[A] Standard Medium (M)**\n"
                        "👉 **[B] Streetwear Oversized Large (L)**\n"
                        "👉 **[C] Boxy Heavyweight XL**\n"
                        "👉 **[D] Other sizes** (XS, S, XXL, 3XL)"
                    ), context, False

            if context.get('color'):
                context['current_step'] = 'awaiting_placement'
                _, menu = self._placement_menu(context['garment'])
                reply = (
                    f"Size block set to **{context['size']}**. Pinned colorway **{context['color']}** loaded.\n\n"
                    "Where are we running your custom design placement?\n\n" + menu
                )
                return reply, context, False

            context['current_step'] = 'awaiting_color'
            reply = (
                f"Size block set to **{context['size']}**. Which fabric premium base colorway are we running with?\n\n"
                "👉 **[A] Jet Black**\n"
                "👉 **[B] Chalk White**\n"
                "👉 **[C] Classic Navy**\n"
                "👉 **[D] Charcoal Grey**"
            )
            return reply, context, False

        # --- STEP 3: COLOR ---
        elif current_step == 'awaiting_color':
            if 'a' in words or 'black' in raw_msg:
                context['color'] = 'Jet Black'
            elif 'b' in words or 'white' in raw_msg:
                context['color'] = 'Chalk White'
            elif 'c' in words or 'navy' in raw_msg:
                context['color'] = 'Classic Navy'
            elif 'd' in words or 'charcoal' in raw_msg:
                context['color'] = 'Charcoal Grey'
            else:
                color_match = words & {'green', 'burgundy', 'cream', 'olive', 'red', 'blue', 'grey', 'gray'}
                if color_match:
                    context['color'] = list(color_match)[0].title()
                else:
                    return (
                        "Select a fabric color option:\n\n"
                        "👉 **[A] Jet Black**\n"
                        "👉 **[B] Chalk White**\n"
                        "👉 **[C] Classic Navy**\n"
                        "👉 **[D] Charcoal Grey**"
                    ), context, False

            context['current_step'] = 'awaiting_placement'
            _, menu = self._placement_menu(context['garment'])
            reply = (
                f"**{context['color']}** base colorway locked down. Where are we running your custom design placement?\n\n"
                + menu
            )
            return reply, context, False

        # --- STEP 4: PLACEMENT ---
        elif current_step == 'awaiting_placement':
            zones = self._get_product_zones(context.get('garment'))
            choice_map = {'a': 0, 'b': 1, 'c': 2, 'd': 3}
            user_msg_idx = None
            override = False

            if 'left' in raw_msg:
                if 'left_side'    in zones: user_msg_idx = zones.index('left_side')
                elif 'left_chest' in zones: user_msg_idx = zones.index('left_chest')
                elif 'left'       in zones: user_msg_idx = zones.index('left')
                else: user_msg_idx = 2 if len(zones) > 2 else 0
                override = True
            elif 'right' in raw_msg:
                if 'right_side' in zones: user_msg_idx = zones.index('right_side')
                elif 'right'    in zones: user_msg_idx = zones.index('right')
                else: user_msg_idx = 3 if len(zones) > 3 else 0
                override = True
            elif 'front' in raw_msg or 'center' in raw_msg or 'centre' in raw_msg:
                user_msg_idx = 0
                override = True
            elif 'back' in raw_msg:
                user_msg_idx = 1 if len(zones) > 1 else 0
                override = True

            selected_zone = None
            if override and user_msg_idx is not None and user_msg_idx < len(zones):
                selected_zone = zones[user_msg_idx]
            else:
                letter_match = words & set(choice_map.keys())
                if letter_match:
                    idx = choice_map[list(letter_match)[0]]
                    if idx < len(zones):
                        selected_zone = zones[idx]

            if not selected_zone:
                for zone in zones:
                    if zone.lower() in raw_msg or zone.lower().replace('_', ' ') in raw_msg:
                        selected_zone = zone
                        break

            if selected_zone:
                context['placement'] = selected_zone
                display_label = self._zone_display(selected_zone)

                # ── NEW: ask what they're printing before triggering upload ──
                context['current_step'] = 'awaiting_design_type'
                reply = (
                    f"Excellent configuration. Your custom blueprint is locked in:\n"
                    f"• Garment: **{context['garment']}**\n"
                    f"• Placement Target: **{display_label}**\n\n"
                    "⚠️ **Important Design Note:**\n"
                    "Custom designs **cannot** be printed directly over our signature HOXOBIL logos or text names. "
                    "We can leave that space clear and print your artwork beautifully around it! ✨\n\n"
                    "Now — what are we printing on this piece? 🎨\n\n"
                    "👉 **[A] Text only** — I'll type out the words I want\n"
                    "👉 **[B] Image / graphic only** — I'll upload a picture or logo\n"
                    "👉 **[C] Both** — I want text AND an image together"
                )
                return reply, context, False

            _, menu = self._placement_menu(context.get('garment'))
            return "I didn't quite catch that. Where should we run your layout placement?\n\n" + menu, context, False

        # --- STEP 5: DESIGN TYPE (NEW) ---
        elif current_step == 'awaiting_design_type':
            if 'a' in words or 'text' in raw_msg or 'words' in raw_msg or 'type' in raw_msg:
                context['design_type'] = 'text'
                context['current_step'] = 'awaiting_upload'
                return (
                    "Got it — text only! ✍️\n\n"
                    "Type out the exact words or phrase you want printed on the garment:"
                ), context, False

            elif 'b' in words or 'image' in raw_msg or 'graphic' in raw_msg or 'logo' in raw_msg or 'picture' in raw_msg or 'upload' in raw_msg:
                context['design_type'] = 'image'
                context['current_step'] = 'awaiting_upload'
                return (
                    "Perfect — image / graphic! 📸\n\n"
                    "Use the attachment icon below to upload your design file (PNG or JPG works best)."
                ), context, True  # True = trigger upload panel

            elif 'c' in words or 'both' in raw_msg:
                context['design_type'] = 'both'
                context['current_step'] = 'awaiting_upload'
                return (
                    "Love it — text AND image! 🔥\n\n"
                    "Start by typing out the words you want printed, then you'll be able to upload your graphic too."
                ), context, False

            else:
                return (
                    "Please pick one of these options:\n\n"
                    "👉 **[A] Text only** — I'll type out the words I want\n"
                    "👉 **[B] Image / graphic only** — I'll upload a picture or logo\n"
                    "👉 **[C] Both** — I want text AND an image together"
                ), context, False

        # --- STEP 6: AWAITING UPLOAD ---
        elif current_step == 'awaiting_upload':
            design_type = context.get('design_type', 'text')

            # Image uploaded
            if '[uploaded design layer asset:' in raw_msg or 'image.png' in raw_msg or 'image.jpg' in raw_msg:
                context['design_asset'] = user_message
                context['current_step'] = 'awaiting_instructions'
                return (
                    "📸 **Artwork Secured!**\n\n"
                    "Do you have any specific text additions, preferred font style, or extra layout instructions? "
                    "(If none, just reply **'No'**)"
                ), context, False

            # Text submitted (for text-only or both)
            else:
                context['custom_text_request'] = user_message
                context['current_step'] = 'awaiting_font'

                # If design type is 'both', after font we'll still trigger upload
                context['needs_upload_after_font'] = (design_type == 'both')

                return (
                    f"Got it: **'{user_message}'** ✍️\n\n"
                    "How do you want the letters to look?\n\n"
                    "👉 **[A] Clean & Simple** — plain, easy-to-read modern text\n"
                    "👉 **[B] Bold & Heavy** — thick, strong streetwear-style lettering\n"
                    "👉 **[C] Flowing Handwriting** — elegant cursive or signature style\n"
                    "👉 **[D] Browse all fonts** — pick a specific font from our full list\n\n"
                    "[FONT_PICKER]"
                ), context, False

        # --- STEP 7: AWAITING FONT ---
        elif current_step == 'awaiting_font':
            font_map = {
                'a': 'Clean & Simple (Sans-Serif)',
                'minimalist': 'Clean & Simple (Sans-Serif)',
                'sans': 'Clean & Simple (Sans-Serif)',
                'clean': 'Clean & Simple (Sans-Serif)',
                'simple': 'Clean & Simple (Sans-Serif)',
                'b': 'Bold & Heavy (Gothic)',
                'bold': 'Bold & Heavy (Gothic)',
                'heavy': 'Bold & Heavy (Gothic)',
                'gothic': 'Bold & Heavy (Gothic)',
                'streetwear': 'Bold & Heavy (Gothic)',
                'c': 'Flowing Handwriting (Script)',
                'flowing': 'Flowing Handwriting (Script)',
                'cursive': 'Flowing Handwriting (Script)',
                'script': 'Flowing Handwriting (Script)',
                'signature': 'Flowing Handwriting (Script)',
                'handwriting': 'Flowing Handwriting (Script)',
                'd': None,  # Browse — handled below
            }

            # Check if a specific font name was sent (from the picker clicking a font)
            # Font picker sends: "[A] FontName" — e.g. "[A] Bebas Neue"
            font_picker_match = re.match(r'\[a\]\s+(.+)', raw_msg)
            if font_picker_match:
                chosen_font = font_picker_match.group(1).strip().title()
                context['font_style'] = chosen_font
                context['font_source'] = 'picker'
            elif 'd' in words and len(words) <= 2:
                # They chose "Browse all fonts" via letter D but picker is already shown
                return (
                    "Browse the full font list above and click the one you like — it'll be selected automatically! 👆"
                ), context, False
            else:
                matched_font = None
                for keyword, font_value in font_map.items():
                    if keyword in words or keyword in raw_msg:
                        matched_font = font_value
                        break

                if not matched_font:
                    return (
                        "Please choose how you want the letters to look:\n\n"
                        "👉 **[A] Clean & Simple** — plain, modern text\n"
                        "👉 **[B] Bold & Heavy** — thick streetwear lettering\n"
                        "👉 **[C] Flowing Handwriting** — cursive / signature style\n"
                        "👉 **[D] Browse all fonts** — pick from our full list\n\n"
                        "[FONT_PICKER]"
                    ), context, False

                context['font_style'] = matched_font

            # If they chose 'both' design type, now trigger the upload panel
            if context.get('needs_upload_after_font'):
                context['current_step'] = 'awaiting_upload'
                context['needs_upload_after_font'] = False
                return (
                    f"Font locked in! ✍️\n\n"
                    "Now upload your graphic or logo using the attachment icon below. 📸"
                ), context, True  # True = trigger upload panel

            context['current_step'] = 'awaiting_instructions'
            return (
                "Typography locked! ✍️\n\n"
                "Any final layout instructions or extra details for the design team? "
                "(If none, just reply **'No'**)"
            ), context, False

        # --- STEP 8: AWAITING INSTRUCTIONS ---
        elif current_step == 'awaiting_instructions':
            if words & APPROVAL_WORDS or any(a in raw_msg for a in ('looks good', 'go ahead', 'no extra', 'none')):
                context['extra_instructions'] = ''
                context['current_step'] = 'ticket_ready'
                return (
                    "✅ **Blueprint Locked In!**\n\n"
                    "I've sent all your specs, assets, and instructions directly to our in-house design team. "
                    "They will review it, draft up a high-quality mockup, and drop it back in this chat for your final approval soon.\n\n"
                    "_(Once the mockup lands, reply **'Approve'** to confirm or type any changes you'd like.)_"
                ), context, False
            else:
                context['extra_instructions'] = user_message
                context['current_step'] = 'ticket_ready'
                return (
                    "✅ **Blueprint Locked In!**\n\n"
                    "I've sent all your specs, assets, and instructions directly to our in-house design team. "
                    "They will review it, draft up a high-quality mockup, and drop it back in this chat for your final approval soon.\n\n"
                    "_(Once the mockup lands, reply **'Approve'** to confirm or type any changes you'd like.)_"
                ), context, False

        # ── KNOWLEDGE BASE CHECK ──────────────────────────────────────────────────
        kb_answer = self._check_knowledge_base(raw_msg, user=user, step=current_step)
        if kb_answer:
            return kb_answer, context, False

        return self.search_web(user_message), context, False


    def _check_knowledge_base(self, raw_msg, user=None, step=''):
        """
        Check BotKnowledge for a matching answer.
        Returns the answer string if found, None otherwise.
        Logs to UnknownQuestion if no match found.
        """
        try:
            from .models import BotKnowledge, UnknownQuestion

            entries = BotKnowledge.objects.filter(is_active=True)
            for entry in entries:
                for keyword in entry.get_keywords_list():
                    if keyword in raw_msg:
                        entry.times_used += 1
                        entry.save(update_fields=['times_used'])
                        return entry.answer

            if len(raw_msg) > 5 and raw_msg not in {'a', 'b', 'c', 'd', 'yes', 'no', 'ok'}:
                UnknownQuestion.objects.get_or_create(
                    message=raw_msg[:500],
                    status='pending',
                    defaults={
                        'user': user,
                        'session_step': step,
                    }
                )
        except Exception:
            pass
        return None


bot = HoxobilChatbot()