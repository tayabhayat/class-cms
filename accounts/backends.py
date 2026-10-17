from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

User = get_user_model()


class RollNumberOrEmailBackend(ModelBackend):
    """
    Authentication backend that supports:
    1. Students logging in via roll number (student_id) or registered email.
    2. Admins logging in via registered email.
    Case-insensitive identifier resolution with whitespace trimming.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        ident = str(username).strip()
        if not ident:
            return None

        user = None

        if "@" in ident:
            # Login via email address
            try:
                user = User.objects.get(email__iexact=ident)
            except (User.DoesNotExist, User.MultipleObjectsReturned):
                user = None
        else:
            # Login via roll number (student_id)
            try:
                user = User.objects.get(student_id__iexact=ident)
            except (User.DoesNotExist, User.MultipleObjectsReturned):
                user = None

        if user:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        else:
            # Run password hasher to prevent user enumeration via timing
            User().set_password(password)

        return None

