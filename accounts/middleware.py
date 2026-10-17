from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class RequirePasswordChangeMiddleware:
    """
    Forces authenticated users with must_change_password=True to change their password
    before accessing any application features. Preserves static assets, logout, and password change.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user

        if (
            user.is_authenticated
            and getattr(user, "role", None) == "student"
            and getattr(user, "must_change_password", False)
        ):
            # Allow static files to ensure CSS, JS, and images load on password change page
            static_url = getattr(settings, "STATIC_URL", "/static/")
            if static_url and request.path.startswith(static_url):
                return self.get_response(request)

            allowed_paths = {
                reverse("password_change"),
                reverse("logout"),
            }

            if request.path not in allowed_paths:
                return redirect("password_change")

        return self.get_response(request)
