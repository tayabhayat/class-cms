
from django.conf import settings
from django.db import models
from django.utils import timezone


class Course(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    semester = models.CharField(max_length=30)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Enrollment(models.Model):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "student"},
        related_name="enrollments",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="enrollments",
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course"],
                name="unique_student_course_enrollment",
            )
        ]

    def __str__(self):
        return f"{self.student.email} - {self.course.code}"

class AttendanceSession(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="attendance_sessions",
    )
    date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_attendance_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["course", "date"],
                name="unique_course_attendance_date",
            )
        ]
        ordering = ["-date"]

    def __str__(self):
        return f"{self.course.code} - {self.date}"


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LEAVE = "leave", "Leave"

    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        related_name="records",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "student"],
                name="unique_student_attendance_session",
            )
        ]

    def __str__(self):
        return f"{self.student.email} - {self.session} - {self.status}"


class Announcement(models.Model):
    title = models.CharField(max_length=200)
    message = models.TextField()
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="announcements",
        null=True,
        blank=True,
        help_text="Course for this notice, or blank for general class-wide announcements.",
    )
    is_published = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Unpublished notices are drafts visible only to Admins/CRs.",
    )
    is_pinned = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Pinned notices appear prominently at the top.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="announcements",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        if self.course:
            return f"[{self.course.code}] {self.title}"
        return self.title


class SubmissionTask(models.Model):
    class DeadlinePolicy(models.TextChoices):
        STRICT = "strict", "Strict deadline"
        ALLOW_LATE = "allow_late", "Allow late submissions"

    class SubmissionType(models.TextChoices):
        FILE = "file", "File upload only"
        TEXT = "text", "Text answer only"
        BOTH = "both", "File or Text (or both)"

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="submission_tasks",
    )
    title = models.CharField(max_length=200)
    instructions = models.TextField(blank=True)
    opens_at = models.DateTimeField()
    deadline = models.DateTimeField()
    deadline_policy = models.CharField(
        max_length=10,
        choices=DeadlinePolicy.choices,
        default=DeadlinePolicy.STRICT,
    )
    submission_type = models.CharField(
        max_length=10,
        choices=SubmissionType.choices,
        default=SubmissionType.FILE,
    )
    max_file_size_mb = models.PositiveIntegerField(
        default=10,
        help_text="Maximum allowed file size in megabytes.",
    )
    allowed_extensions = models.CharField(
        max_length=200,
        default=".pdf,.zip,.py,.txt,.docx",
        blank=True,
        help_text="Comma-separated allowed extensions (e.g. .pdf,.zip,.py).",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_submission_tasks",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-deadline", "-created_at"]

    def __str__(self):
        return f"{self.course.code} - {self.title}"

    def is_open(self):
        return timezone.now() >= self.opens_at

    def is_past_deadline(self):
        return timezone.now() > self.deadline

    def can_submit(self):
        now = timezone.now()
        if now < self.opens_at:
            return False
        if self.deadline_policy == self.DeadlinePolicy.STRICT:
            return now <= self.deadline
        return True

    def get_allowed_extensions_list(self):
        if not self.allowed_extensions:
            return []
        return [
            ext.strip().lower()
            for ext in self.allowed_extensions.split(",")
            if ext.strip()
        ]


class Submission(models.Model):
    task = models.ForeignKey(
        SubmissionTask,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    file = models.FileField(
        upload_to="submissions/%Y/%m/",
        blank=True,
        null=True,
    )
    original_filename = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    text_answer = models.TextField(
        blank=True,
        default="",
    )
    is_late = models.BooleanField(
        default=False,
    )
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-uploaded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "student"],
                name="unique_student_task_submission",
            )
        ]

    def __str__(self):
        return f"{self.student.email} - {self.task.title}"


class Poll(models.Model):
    class ResultsVisibility(models.TextChoices):
        ALWAYS = "always", "Visible after voting"
        AFTER_CLOSE = "after_close", "Visible only after poll closes"

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="polls",
        null=True,
        blank=True,
        help_text="Leave blank if poll applies to all students, or select a course.",
    )
    question = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    is_closed = models.BooleanField(default=False)
    results_visibility = models.CharField(
        max_length=15,
        choices=ResultsVisibility.choices,
        default=ResultsVisibility.ALWAYS,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_polls",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.question

    def is_active(self):
        now = timezone.now()
        return not self.is_closed and (self.opens_at <= now <= self.closes_at)

    def is_expired(self):
        now = timezone.now()
        return self.is_closed or (now > self.closes_at)

    def has_user_voted(self, user):
        if not user or not user.is_authenticated:
            return False
        return self.votes.filter(student=user).exists()

    def can_user_view_results(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.role == "admin":
            return True
        if self.is_expired():
            return True
        if self.results_visibility == self.ResultsVisibility.ALWAYS:
            return self.has_user_voted(user)
        return False

    @property
    def total_votes(self):
        return self.votes.count()


class PollOption(models.Model):
    poll = models.ForeignKey(
        Poll,
        on_delete=models.CASCADE,
        related_name="options",
    )
    text = models.CharField(max_length=200)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.poll.question} - {self.text}"

    @property
    def vote_count(self):
        return self.votes.count()

    def vote_percentage(self):
        total = self.poll.total_votes
        if total == 0:
            return 0
        return round((self.vote_count / total) * 100, 1)


class PollVote(models.Model):
    poll = models.ForeignKey(
        Poll,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    option = models.ForeignKey(
        PollOption,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="poll_votes",
    )
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["poll", "student"],
                name="unique_student_poll_vote",
            )
        ]

    def __str__(self):
        return f"{self.student.email} voted in {self.poll.question}"


class CourseResource(models.Model):
    class Category(models.TextChoices):
        LECTURE_NOTES = "lecture_notes", "Lecture Slides & Notes"
        BOOK = "book", "Textbooks & Reference Books"
        STUDY_GUIDE = "study_guide", "Study Guides & Past Papers"
        OTHER = "other", "Other Educational Material"

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="resources",
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=50,
        choices=Category.choices,
        default=Category.LECTURE_NOTES,
    )
    file = models.FileField(upload_to="course_resources/%Y/%m/")
    original_filename = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveIntegerField(
        default=0,
        help_text="File size in bytes.",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_resources",
    )
    is_published = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Unpublished resources are drafts visible only to Admins/CRs.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "-created_at"]

    def __str__(self):
        return f"{self.course.code} - {self.title}"

    def file_size_display(self):
        if not self.file_size:
            return "0 KB"
        if self.file_size < 1024 * 1024:
            return f"{round(self.file_size / 1024, 1)} KB"
        return f"{round(self.file_size / (1024 * 1024), 2)} MB"


class CourseLink(models.Model):
    class Category(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp / Discussion Group"
        VIDEO = "video", "Lecture Video / Playlist"
        WEBSITE = "website", "Course / Official Website"
        REFERENCE = "reference", "Reference & Documentation"
        OTHER = "other", "Other Link"

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="links",
    )
    title = models.CharField(max_length=200)
    url = models.URLField(max_length=500)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=50,
        choices=Category.choices,
        default=Category.REFERENCE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_course_links",
    )
    is_published = models.BooleanField(
        default=True,
        db_index=True,
        help_text="Unpublished links are drafts visible only to Admins/CRs.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "-created_at"]

    def __str__(self):
        return f"{self.course.code} - {self.title}"


class Notification(models.Model):
    class ContentType(models.TextChoices):
        ANNOUNCEMENT = "announcement", "Course Notice"
        RESOURCE = "resource", "Study Material"
        LINK = "link", "Useful Link"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=200)
    message = models.CharField(max_length=500)
    target_url = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    content_type = models.CharField(max_length=50, blank=True)
    content_id = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "content_type", "content_id"],
                name="unique_recipient_content_notification",
            )
        ]
        indexes = [
            models.Index(fields=["recipient", "is_read", "-created_at"]),
        ]

    def __str__(self):
        return f"Notification for {self.recipient.email}: {self.title}"
