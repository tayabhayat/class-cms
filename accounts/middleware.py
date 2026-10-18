from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone


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


class UserActivityMiddleware:
    """
    Tracks authenticated users' last_activity timestamp on user requests.
    Updates are throttled to at most once every 60 seconds (1 minute) per user
    to prevent high-frequency database writes on rapid navigation.
    The throttle is based on the persistent User.last_activity timestamp, ensuring
    it works reliably across multiple concurrent sessions for the same user.
    Anonymous visitors and static assets are never tracked.
    """

    THROTTLE_SECONDS = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if user and user.is_authenticated:
            static_url = getattr(settings, "STATIC_URL", "/static/")
            media_url = getattr(settings, "MEDIA_URL", "/media/")
            path = request.path

            if not (
                (static_url and path.startswith(static_url))
                or (media_url and path.startswith(media_url))
            ):
                now = timezone.now()
                last_activity = getattr(user, "last_activity", None)

                if last_activity is not None and timezone.is_naive(last_activity):
                    last_activity = timezone.make_aware(last_activity)

                # Determine if an update is needed by checking user's persistent last_activity.
                # If last_activity is None (no prior activity), record immediately.
                # If last_activity is set, only update if at least THROTTLE_SECONDS have elapsed.
                should_update = False
                if last_activity is None:
                    should_update = True
                else:
                    elapsed = (now - last_activity).total_seconds()
                    if elapsed >= self.THROTTLE_SECONDS:
                        should_update = True

                if should_update:
                    from .models import User

                    User.objects.filter(id=user.id).update(last_activity=now)
                    user.last_activity = now
                    request.session["last_activity_update_ts"] = now.timestamp()

        return self.get_response(request)
