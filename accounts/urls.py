from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import (
    ClassLoginView,
    RequiredPasswordChangeView,
    forgot_password,
    verify_reset_code,
    reset_password,
    admin_student_list,
    admin_reset_student_password,
    admin_reset_student_password_done,
    active_users_view,
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
    # Administrative Routes
    path("active-users/", active_users_view, name="active_users"),
    path("students/", admin_student_list, name="admin_student_list"),
    path(
        "students/<int:student_id>/reset-password/",
        admin_reset_student_password,
        name="admin_reset_student_password",
    ),
    path(
        "students/reset-password/done/",
        admin_reset_student_password_done,
        name="admin_reset_student_password_done",
    ),
]
