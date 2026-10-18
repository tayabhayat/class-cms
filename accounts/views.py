import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.cache import never_cache

from .forms import (
    ClassLoginForm,
    ForgotPasswordRequestForm,
    ResetPasswordForm,
    VerifyResetCodeForm,
)
from .models import PasswordResetAudit, PasswordResetCode

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


def is_admin(user):
    """Permission helper verifying if an authenticated user is an Admin/CR."""
    return user.is_authenticated and getattr(user, "role", None) == "admin"


def generate_temporary_password(length=14):
    """
    Generates a cryptographically secure, unpredictable temporary password
    guaranteed to contain uppercase, lowercase, digits, and safe special characters.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*" for c in password)
        ):
            return password


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


def get_client_ip(request):
    """Safely extracts client IP address from request META."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "127.0.0.1")


def forgot_password(request):
    """
    Informs students that self-service email recovery has been replaced with
    administrator-assisted password resets. Instructs them to contact their CR/Admin.
    """
    if request.user.is_authenticated:
        return redirect("dashboard")

    return render(request, "registration/forgot_password.html")


def verify_reset_code(request):
    """Legacy endpoint redirecting to the helpful contact-admin guidance page."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    messages.info(
        request,
        "Self-service email recovery is discontinued. Please contact your Class Representative or Course Administrator to reset your password.",
    )
    return redirect("forgot_password")


def reset_password(request):
    """Legacy endpoint redirecting to the helpful contact-admin guidance page."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    messages.info(
        request,
        "Self-service email recovery is discontinued. Please contact your Class Representative or Course Administrator to reset your password.",
    )
    return redirect("forgot_password")


@login_required
@user_passes_test(is_admin)
def admin_student_list(request):
    """
    Displays the list of enrolled students for authorized Admins/CRs.
    Allows searching by roll number, name, or email, and provides a direct
    action to initiate an administrative password reset.
    """
    query = request.GET.get("q", "").strip()
    students_qs = User.objects.filter(role=User.Role.STUDENT)

    if query:
        students_qs = students_qs.filter(
            Q(student_id__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
        )

    students = list(students_qs.order_by("student_id", "first_name", "email"))

    # Annotate latest reset timestamp from audit records without N+1 queries
    latest_resets = dict(
        PasswordResetAudit.objects.filter(target_user__in=students)
        .values("target_user_id")
        .annotate(last_reset=Max("timestamp"))
        .values_list("target_user_id", "last_reset")
    )

    for student in students:
        student.last_reset_time = latest_resets.get(student.id)

    return render(
        request,
        "accounts/admin_student_list.html",
        {
            "students": students,
            "query": query,
            "total_students": len(students),
        },
    )


@login_required
@user_passes_test(is_admin)
def admin_reset_student_password(request, student_id):
    """
    Administrative password reset view.
    GET: Displays explicit confirmation with student details.
    POST: Generates a cryptographically secure temporary password, hashes it using
          Django's password hasher, sets must_change_password=True, records an audit log
          (without storing any password or secret), and stores the temporary password in
          the session for a one-time view.
    """
    student = get_object_or_404(User, id=student_id)

    # Reject non-student accounts (Admins cannot reset other admins via this workflow)
    if student.role != User.Role.STUDENT:
        messages.error(
            request,
            "Unauthorized operation. Only student accounts can be reset through this interface.",
        )
        return redirect("admin_student_list")

    if request.method == "POST":
        # Generate cryptographically secure temporary password
        temp_password = generate_temporary_password()

        # Execute database operations atomically to prevent partial resets
        with transaction.atomic():
            # Set hashed password and force first-login change
            student.set_password(temp_password)
            student.must_change_password = True
            student.save(update_fields=["password", "must_change_password"])

            # Audit the action without storing any password or secret
            PasswordResetAudit.objects.create(
                reset_by=request.user,
                target_user=student,
                ip_address=get_client_ip(request),
            )

            # Invalidate any legacy unconsumed reset codes for safety
            PasswordResetCode.objects.filter(user=student, is_consumed=False).update(is_consumed=True)

        # Store temporary password in session for one-time display only after transaction commits
        request.session["admin_temp_password_display"] = {
            "student_id": student.student_id or "—",
            "student_name": student.get_full_name() or student.email,
            "student_email": student.email,
            "temp_password": temp_password,
        }

        return redirect("admin_reset_student_password_done")

    # GET: Render confirmation screen
    return render(
        request,
        "accounts/admin_reset_password_confirm.html",
        {
            "student": student,
        },
    )


@login_required
@user_passes_test(is_admin)
@never_cache
def admin_reset_student_password_done(request):
    """
    Displays the temporary password to the authorized administrator ONLY ONCE.
    Popped from the session immediately upon retrieval so it cannot be viewed again.
    Applies strict Cache-Control: no-store and Pragma: no-cache headers.
    """
    reset_data = request.session.pop("admin_temp_password_display", None)
    if not reset_data:
        messages.info(
            request,
            "The temporary password has already been cleared or expired. Temporary passwords are only displayed once.",
        )
        return redirect("admin_student_list")

    response = render(
        request,
        "accounts/admin_reset_password_done.html",
        {
            "reset_data": reset_data,
        },
    )
    # Strictly prevent client, intermediary, or proxy caching of temporary credentials
    response["Cache-Control"] = "no-cache, no-store, must-revalidate, private"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


ACTIVE_NOW_THRESHOLD_MINUTES = 5


@login_required
@user_passes_test(is_admin)
def active_users_view(request):
    """
    Renders the active users dashboard for Admin/CR users.
    Displays authenticated users with their name, role, student ID, and last activity time.
    Users with authenticated activity within the last 5 minutes are classified as 'Active now'.
    """
    now = timezone.now()
    active_threshold = now - timedelta(minutes=ACTIVE_NOW_THRESHOLD_MINUTES)

    role_filter = request.GET.get("role", "").strip().lower()
    search_query = request.GET.get("q", "").strip()

    users_qs = User.objects.all().order_by("-last_activity", "first_name", "email")

    if role_filter in {User.Role.ADMIN, User.Role.STUDENT}:
        users_qs = users_qs.filter(role=role_filter)

    if search_query:
        users_qs = users_qs.filter(
            Q(first_name__icontains=search_query)
            | Q(last_name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(student_id__icontains=search_query)
        )

    total_users_count = User.objects.count()
    active_now_count = User.objects.filter(last_activity__gte=active_threshold).count()
    active_today_count = User.objects.filter(last_activity__date=now.date()).count()

    users_list = []
    for u in users_qs:
        is_active_now = (
            u.last_activity is not None and u.last_activity >= active_threshold
        )
        users_list.append({
            "user": u,
            "student_id": u.student_id or "—",
            "name": u.get_full_name() or u.email,
            "email": u.email,
            "role": u.role,
            "role_display": u.get_role_display(),
            "last_activity": u.last_activity,
            "is_active_now": is_active_now,
        })

    context = {
        "users": users_list,
        "total_users_count": total_users_count,
        "active_now_count": active_now_count,
        "active_today_count": active_today_count,
        "active_threshold_minutes": ACTIVE_NOW_THRESHOLD_MINUTES,
        "search_query": search_query,
        "role_filter": role_filter,
    }

    return render(request, "accounts/active_users.html", context)
