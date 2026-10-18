
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("An email address is required")

        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "admin")

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")

        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "CR / Admin"
        STUDENT = "student", "Student"

    username = None

    email = models.EmailField(unique=True)
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.STUDENT,
    )
    must_change_password = models.BooleanField(default=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return f"{self.email} ({self.role})"

    student_id = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
    )
    last_activity = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Timestamp of the user's most recent authenticated activity on the CMS.",
    )


class PasswordResetCode(models.Model):
    MAX_ATTEMPTS = 5
    EXPIRY_MINUTES = 10

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="password_reset_codes",
    )
    code_hash = models.CharField(max_length=128)
    session_token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts_count = models.PositiveIntegerField(default=0)
    is_verified = models.BooleanField(default=False)
    is_consumed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PasswordResetCode for {self.user.email} (expires {self.expires_at})"

    @classmethod
    def hash_code(cls, raw_code: str, session_token: str) -> str:
        import hashlib
        return hashlib.sha256(f"{raw_code.strip()}:{session_token}".encode("utf-8")).hexdigest()

    def is_expired(self) -> bool:
        from django.utils import timezone
        return timezone.now() > self.expires_at

    def is_valid(self) -> bool:
        return (
            not self.is_consumed
            and not self.is_expired()
            and self.attempts_count < self.MAX_ATTEMPTS
        )

    def check_code(self, raw_code: str) -> bool:
        import secrets
        if not self.is_valid():
            return False
        expected_hash = self.hash_code(raw_code, self.session_token)
        return secrets.compare_digest(self.code_hash, expected_hash)


class PasswordResetAudit(models.Model):
    """
    Audits administrative password resets. Records who performed the reset,
    which student was reset, and the timestamp.
    STRICT SECURITY CONSTRAINT: Absolutely NO passwords, hashes, or verification
    secrets are stored in this model.
    """
    reset_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="performed_resets",
    )
    target_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="received_resets",
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"Password reset for {self.target_user} by {self.reset_by} at {self.timestamp}"
