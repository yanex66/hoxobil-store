from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views

urlpatterns = [
    # Custom Password Reset Routes
    path('accounts/password-reset/',         
         auth_views.PasswordResetView.as_view(
             template_name='registration/password_reset_form.html',
             email_template_name='registration/password_reset_email.txt',
             subject_template_name='registration/password_reset_subject.txt',
         ),         
         name='password_reset'),
         
    path('accounts/password-reset/done/',         
         auth_views.PasswordResetDoneView.as_view(
             template_name='registration/password_reset_done.html',
         ),         
         name='password_reset_done'),
         
    path('accounts/reset/<uidb64>/<token>/',         
         auth_views.PasswordResetConfirmView.as_view(
             template_name='registration/password_reset_confirm.html',
         ),         
         name='password_reset_confirm'),
         
    path('accounts/reset/done/',         
         auth_views.PasswordResetCompleteView.as_view(
             template_name='registration/password_reset_complete.html',
         ),         
         name='password_reset_complete'),

    # Override password change BEFORE accounts/ include
    path('accounts/password_change/',
         auth_views.PasswordChangeView.as_view(
             template_name='registration/password_change_form.html',
             success_url='/accounts/password_change/done/',
         ),
         name='password_change'),

    path('accounts/password_change/done/',
         auth_views.PasswordChangeDoneView.as_view(
             template_name='registration/password_change_done.html',
         ),
         name='password_change_done'),

    # Admin Site
    path('admin/', admin.site.urls),
    
    # Shop app
    path('', include('shop.urls')), 
    
    # User accounts (Handles default login/logout)
    path('accounts/', include('django.contrib.auth.urls')), 
]

# Serve static and user-uploaded media configurations during active local development loops
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)