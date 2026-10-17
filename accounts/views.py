import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.mail import send_mail
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone

from .forms import (
    ClassLoginForm,
    ForgotPasswordRequestForm,
    ResetPasswordForm,
    VerifyResetCodeForm,
)
from .models import PasswordResetCode

User = get_user_model()


def mask_email(email: str) -> str:
    """Mask email for display, e.g., 'student@pucit.edu.pk' -> 's***t@pucit.edu.pk'."""
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + ("*" * (len(local) - 2)) + local[-1]
    return f"{masked_local}@{domain}"


class ClassLoginView(LoginView):
    authentication_form = ClassLoginForm
    template_name = "registration/login.html"
    redirect_authenticated_user = True


class RequiredPasswordChangeView(PasswordChangeView):
    template_name = "registration/password_change.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        user = self.request.user
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])

        messages.success(
            self.request,
            "Your password has been changed successfully.",
        )
        return response


from django.core.cache import cache


def get_client_ip(request):
    """Safely extracts client IP address from request META."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def forgot_password(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = ForgotPasswordRequestForm(request.POST)
        if form.is_valid():
            # IP-level rate limiting: max 5 requests per minute per IP
            client_ip = get_client_ip(request)
            ip_cache_key = f"pwd_reset_ip_{client_ip}"
            ip_count = cache.get(ip_cache_key, 0)
            if ip_count >= 5:
                messages.error(
                    request,
                    "Too many password reset requests from your network. Please wait a minute before trying again.",
                )
                return render(request, "registration/forgot_password.html", {"form": form})
            cache.set(ip_cache_key, ip_count + 1, timeout=60)

            ident = form.cleaned_data["identifier"]
            user = None

            if "@" in ident:
                user = User.objects.filter(email__iexact=ident, is_active=True).first()
            else:
                user = User.objects.filter(student_id__iexact=ident, is_active=True).first()

            # Generic success message to prevent user enumeration
            generic_message = (
                "If an account matches that roll number or email, a 6-digit verification "
                "code has been sent to the registered university email address."
            )

            if user:
                # Rate limiting check: check if code created within last 60 seconds
                is_recent = PasswordResetCode.objects.filter(
                    user=user,
                    created_at__gte=timezone.now() - timedelta(seconds=60),
                    is_consumed=False,
                ).exists()

                if is_recent:
                    if "reset_session_token" not in request.session:
                        request.session["reset_session_token"] = secrets.token_urlsafe(32)
                    messages.info(request, generic_message)
                    return redirect("verify_reset_code")

                # Invalidate any existing active codes for this user
                PasswordResetCode.objects.filter(user=user, is_consumed=False).update(is_consumed=True)

                raw_code = f"{secrets.randbelow(900000) + 100000}"
                session_token = secrets.token_urlsafe(32)
                code_hash = PasswordResetCode.hash_code(raw_code, session_token)

                reset_code = PasswordResetCode(
                    user=user,
                    code_hash=code_hash,
                    session_token=session_token,
                    expires_at=timezone.now() + timedelta(minutes=PasswordResetCode.EXPIRY_MINUTES),
                )

                subject = "PUCIT Class CMS — Password Reset Verification Code"
                body = (
                    f"Hello {user.get_full_name() or user.email},\n\n"
                    f"You requested to reset your password for the PUCIT Class Management System.\n\n"
                    f"Your 6-digit verification code is:\n\n"
                    f"    {raw_code}\n\n"
                    f"This code will expire in {PasswordResetCode.EXPIRY_MINUTES} minutes.\n\n"
                    f"If you did not request a password reset, please ignore this email or notify your CR/Admin.\n\n"
                    f"Regards,\nPUCIT Class CMS"
                )

                try:
                    send_mail(
                        subject=subject,
                        message=body,
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@pucit.edu.pk"),
                        recipient_list=[user.email],
                        fail_silently=False,
                    )
                    reset_code.save()
                    request.session["reset_session_token"] = session_token
                    request.session["reset_masked_email"] = mask_email(user.email)
                except Exception:
                    # Do not create usable reset code on email delivery failure
                    messages.error(
                        request,
                        "Unable to deliver the verification code email at this time. "
                        "Please check the email service configuration or try again later.",
                    )
                    return render(request, "registration/forgot_password.html", {"form": form})
            else:
                # Generate a dummy token to prevent timing or enumeration differences
                dummy_token = secrets.token_urlsafe(32)
                request.session["reset_session_token"] = dummy_token
                request.session.pop("reset_masked_email", None)

            messages.info(request, generic_message)
            return redirect("verify_reset_code")
    else:
        form = ForgotPasswordRequestForm()

    return render(request, "registration/forgot_password.html", {"form": form})


def verify_reset_code(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    session_token = request.session.get("reset_session_token")
    if not session_token:
        messages.warning(request, "Please request a password reset code first.")
        return redirect("forgot_password")

    masked_email = request.session.get("reset_masked_email", "")

    if request.method == "POST":
        form = VerifyResetCodeForm(request.POST)
        if form.is_valid():
            raw_code = form.cleaned_data["code"]
            reset_code = PasswordResetCode.objects.filter(
                session_token=session_token,
                is_consumed=False,
            ).first()

            if not reset_code:
                form.add_error(
                    None,
                    "Invalid or expired verification session. Please request a new code.",
                )
            elif reset_code.is_expired():
                form.add_error(
                    "code",
                    "This verification code has expired (10-minute limit). Please request a new code.",
                )
            elif reset_code.attempts_count >= reset_code.MAX_ATTEMPTS:
                reset_code.is_consumed = True
                reset_code.save(update_fields=["is_consumed"])
                form.add_error(
                    "code",
                    "Maximum verification attempts exceeded (5 attempts). Please request a new code.",
                )
            else:
                if reset_code.check_code(raw_code):
                    reset_code.is_verified = True
                    reset_code.save(update_fields=["is_verified"])
                    request.session["reset_verified_token"] = session_token
                    messages.success(request, "Verification code confirmed. Please set your new password.")
                    return redirect("reset_password")
                else:
                    reset_code.attempts_count += 1
                    if reset_code.attempts_count >= reset_code.MAX_ATTEMPTS:
                        reset_code.is_consumed = True
                        reset_code.save(update_fields=["attempts_count", "is_consumed"])
                        form.add_error(
                            "code",
                            "Maximum verification attempts exceeded (5 attempts). Please request a new code.",
                        )
                    else:
                        reset_code.save(update_fields=["attempts_count"])
                        remaining = reset_code.MAX_ATTEMPTS - reset_code.attempts_count
                        form.add_error(
                            "code",
                            f"Incorrect verification code. You have {remaining} attempt(s) remaining.",
                        )
    else:
        form = VerifyResetCodeForm()

    return render(
        request,
        "registration/verify_reset_code.html",
        {"form": form, "masked_email": masked_email},
    )


def reset_password(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    session_token = request.session.get("reset_verified_token")
    if not session_token:
        messages.warning(request, "Please verify your code before resetting your password.")
        return redirect("forgot_password")

    reset_code = PasswordResetCode.objects.filter(
        session_token=session_token,
        is_verified=True,
        is_consumed=False,
    ).first()

    if not reset_code or reset_code.is_expired():
        messages.error(
            request,
            "Your password reset authorization has expired or is invalid. Please request a new code.",
        )
        return redirect("forgot_password")

    user = reset_code.user

    if request.method == "POST":
        form = ResetPasswordForm(user=user, data=request.POST)
        if form.is_valid():
            form.save()

            # Mark verification code consumed
            reset_code.is_consumed = True
            reset_code.save(update_fields=["is_consumed"])

            # User just set a new private password, so they don't need first-login change again
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])

            # Clear reset session tokens
            request.session.pop("reset_session_token", None)
            request.session.pop("reset_verified_token", None)
            request.session.pop("reset_masked_email", None)

            messages.success(
                request,
                "Your password has been reset successfully. Please log in with your new password.",
            )
            return redirect("login")
    else:
        form = ResetPasswordForm(user=user)

    return render(
        request,
        "registration/reset_password.html",
        {
            "form": form,
            "user_display": user.get_full_name() or user.student_id or user.email,
        },
    )
