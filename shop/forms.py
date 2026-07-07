from django import forms
from .models import Order, Review

class CheckoutForm(forms.ModelForm):
    """
    Form for capturing global shipping details.
    Uses 'hox-input' for CSS styling and handles the new fields added to the Order model.
    """
    
    # 🌍 TOP GLOBAL MARKETS (Printify prioritized)
    COUNTRY_CHOICES = [
        ('NG', 'Nigeria'),
        ('US', 'United States'),
        ('GB', 'United Kingdom'),
        ('CA', 'Canada'),
        ('GH', 'Ghana'),
        ('DE', 'Germany'),
        ('FR', 'France'),
    ]

    # Explicitly defining country as a ChoiceField for the dropdown
    country = forms.ChoiceField(
        choices=COUNTRY_CHOICES, 
        initial='NG',
        widget=forms.Select(attrs={'class': 'form-select hox-input'})
    )

    class Meta:
        model = Order
        # These fields MUST exist in your models.py Order class
        fields = [
            'first_name', 
            'last_name', 
            'email', 
            'phone', 
            'address', 
            'city', 
            'state', 
            'country', 
            'postal_code'
        ]
        
        # Adding placeholders and glossy classes to all fields
        widgets = {
            'first_name': forms.TextInput(attrs={'placeholder': 'Enter first name', 'class': 'hox-input'}),
            'last_name': forms.TextInput(attrs={'placeholder': 'Enter last name', 'class': 'hox-input'}),
            'email': forms.EmailInput(attrs={'placeholder': 'Email for notifications', 'class': 'hox-input'}),
            'phone': forms.TextInput(attrs={'placeholder': 'Required for delivery', 'class': 'hox-input'}),
            'address': forms.TextInput(attrs={'placeholder': 'Street name and house number', 'class': 'hox-input'}),
            'city': forms.TextInput(attrs={'placeholder': 'City', 'class': 'hox-input'}),
            'state': forms.TextInput(attrs={'placeholder': 'Province / State / Region', 'class': 'hox-input'}),
            'postal_code': forms.TextInput(attrs={'placeholder': 'Postal code', 'class': 'hox-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Ensure all CharFields use the standard Bootstrap control + our custom hox-input
        for field in self.fields:
            existing_classes = self.fields[field].widget.attrs.get('class', '')
            if 'form-select' not in existing_classes:
                self.fields[field].widget.attrs.update({'class': f'form-control {existing_classes}'})

class ReviewForm(forms.ModelForm):
    """
    Verified-purchase product review form. The view enforces the purchase
    check and the (product, user) uniqueness — this form only validates
    the fields the customer actually fills in.
    """

    class Meta:
        model = Review
        fields = ['rating', 'title', 'comment']
        widgets = {
            'rating': forms.Select(
                choices=Review.RATING_CHOICES,
                attrs={'class': 'form-select hox-input'}
            ),
            'title': forms.TextInput(attrs={
                'placeholder': 'Give your review a title (optional)',
                'class': 'form-control hox-input',
            }),
            'comment': forms.Textarea(attrs={
                'placeholder': 'Share your experience with this product...',
                'rows': 4,
                'class': 'form-control hox-input',
            }),
        }