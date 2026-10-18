from .models import Notification


def notification_context(request):
    """Provides unread_notifications_count for authenticated users."""
    if hasattr(request, "user") and request.user.is_authenticated:
        count = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count()
        return {"unread_notifications_count": count}
    return {"unread_notifications_count": 0}
