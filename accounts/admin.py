from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, PasswordResetCode



@admin.register(User)
class CustomUserAdmin(UserAdmin):
    
    ordering = ("email",)
    list_display = (
    "student_id",
    "full_name",
    "enrolled_courses",
    "email",
    "role",
    "is_staff",
    "is_active",
    )
    search_fields = (
    "student_id",
    "email",
    "name",
    "full_name",
    )

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {
            "fields": ("first_name", "last_name", "student_id")
        }),
        ("Permissions", {
            "fields": (
                "role",
                "must_change_password",
                "is_active",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            )
        }),
        ("Important dates", {
            "fields": ("last_login", "date_joined")
        }),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "email",
                "student_id",
                "password1",
                "password2",
                "role",
            ),
        }),
    )
    @admin.display(description="Full Name")
    def full_name(self, obj):
        return obj.get_full_name() or obj.email

    @admin.display(description="Enrolled Courses")
    def enrolled_courses(self, obj):
        if obj.role != "student":
            return "—"

        courses = obj.enrollments.select_related("course").all()
        course_list = [
            f"{enrollment.course.code} - {enrollment.course.name}"
            for enrollment in courses
        ]

        return ", ".join(course_list) if course_list else "Not enrolled"


@admin.register(PasswordResetCode)
class PasswordResetCodeAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "created_at",
        "expires_at",
        "attempts_count",
        "is_verified",
        "is_consumed",
    )
    list_filter = ("is_verified", "is_consumed", "created_at")
    search_fields = ("user__email", "user__student_id")
    readonly_fields = ("code_hash", "session_token", "created_at", "expires_at")

