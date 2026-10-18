from django.contrib import admin
from .models import (
    Course,
    Enrollment,
    AttendanceSession,
    Attendance,
    Announcement,
    CourseResource,
    CourseLink,
    Notification,
    SubmissionTask,
    Submission,
    Poll,
    PollOption,
    PollVote,
)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "semester", "is_active")
    search_fields = ("code", "name")
    list_filter = ("semester", "is_active")



@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = (
        "student_name",
        "student_id",
        "student_email",
        "course",
        "enrolled_at",
    )
    list_filter = ("course",)
    search_fields = (
        "student__first_name",
        "student__last_name",
        "student__student_id",
        "student__email",
        "course__code",
        "course__name",
    )
    list_select_related = ("student", "course")
    ordering = ("course__code", "student__student_id")

    @admin.display(description="Student Name", ordering="student__first_name")
    def student_name(self, obj):
        return obj.student.get_full_name() or obj.student.email

    @admin.display(description="Student ID", ordering="student__student_id")
    def student_id(self, obj):
        return obj.student.student_id or "—"

    @admin.display(description="Email", ordering="student__email")
    def student_email(self, obj):
        return obj.student.email


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("course", "date", "created_by")
    list_filter = ("course", "date")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("student", "session", "status")
    list_filter = ("status", "session__course")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "is_published", "is_pinned", "created_by", "created_at")
    list_filter = ("course", "is_published", "is_pinned", "created_at")
    search_fields = ("title", "message", "course__code", "course__name")


@admin.register(CourseResource)
class CourseResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "category", "file_size_display", "is_published", "uploaded_by", "created_at")
    list_filter = ("course", "category", "is_published", "created_at")
    search_fields = ("title", "description", "original_filename", "course__code")


@admin.register(CourseLink)
class CourseLinkAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "category", "url", "is_published", "created_by", "created_at")
    list_filter = ("course", "category", "is_published", "created_at")
    search_fields = ("title", "description", "url", "course__code")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "title", "course", "is_read", "content_type", "created_at")
    list_filter = ("is_read", "content_type", "course", "created_at")
    search_fields = ("recipient__email", "title", "message")


@admin.register(SubmissionTask)
class SubmissionTaskAdmin(admin.ModelAdmin):
    list_display = (
        "title", "course", "deadline", "deadline_policy", "submission_type"
    )
    list_filter = ("course", "deadline_policy", "submission_type")
    search_fields = ("title", "course__code", "course__name")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("student", "task", "is_late", "uploaded_at")
    list_filter = ("task__course", "is_late")
    search_fields = ("student__email", "task__title")


class PollOptionInline(admin.TabularInline):
    model = PollOption
    extra = 2


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = (
        "question", "course", "opens_at", "closes_at", "is_closed", "results_visibility", "total_votes"
    )
    list_filter = ("is_closed", "results_visibility", "course")
    search_fields = ("question", "description")
    inlines = [PollOptionInline]


@admin.register(PollVote)
class PollVoteAdmin(admin.ModelAdmin):
    list_display = ("poll", "student", "option", "voted_at")
    list_filter = ("poll",)
    search_fields = ("student__email", "poll__question")