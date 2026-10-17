from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import (
    ClassLoginView,
    RequiredPasswordChangeView,
    forgot_password,
    verify_reset_code,
    reset_password,
)

urlpatterns = [
    path("login/", ClassLoginView.as_view(), name="login"),
    path(
        "password-change/",
        RequiredPasswordChangeView.as_view(),
        name="password_change",
    ),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("forgot-password/", forgot_password, name="forgot_password"),
    path("verify-code/", verify_reset_code, name="verify_reset_code"),
    path("reset-password/", reset_password, name="reset_password"),
]
