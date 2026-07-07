import requests
from django.conf import settings
from datetime import datetime, timedelta

# Simple cache structure for demonstration; for production, use Django Cache or Redis.
CURRENCY_CACHE = {
    'data': None,
    'expiry': datetime.min
}

def get_printify_supported_currencies(force_refresh=False):
    """
    Simulates fetching the list of supported currencies from the Printify API.
    
    Note: The Printify API does not have a single /currencies endpoint.
    This function simulates a successful response based on common Printify currency support.
    
    A caching mechanism is included to prevent excessive API calls.
    """
    
    if not settings.PRINTIFY_ACCESS_TOKEN:
        print("WARNING: PRINTIFY_ACCESS_TOKEN is not set. Using fallback currencies.")
        # Fallback list used if API token is missing
        return ['USD', 'EUR', 'GBP'] 
    
    # Check cache
    if not force_refresh and CURRENCY_CACHE['data'] and CURRENCY_CACHE['expiry'] > datetime.now():
        return CURRENCY_CACHE['data']

    # --- API Call Simulation ---
    try:
        # Added NGN to the supported list
        supported_currencies = ['USD', 'EUR', 'GBP', 'CAD', 'AUD', 'JPY', 'NGN']
        
        # Update cache
        CURRENCY_CACHE['data'] = supported_currencies
        CURRENCY_CACHE['expiry'] = datetime.now() + timedelta(hours=1) # Cache for 1 hour
        
        return supported_currencies

    except requests.RequestException as e:
        print(f"Error fetching Printify currencies: {e}")
        # Return fallback on failure
        return ['USD', 'EUR', 'GBP']