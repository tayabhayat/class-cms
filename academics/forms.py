
import os
from urllib.parse import urlparse

from django import forms
from .models import (
    Course,
    Announcement,
    CourseResource,
    CourseLink,
    SubmissionTask,
    Submission,
    Poll,
    PollOption,
)


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["code", "name", "semester", "is_active"]
        labels = {
            "code": "Course Code",
            "name": "Course Name",
            "semester": "Semester",
            "is_active": "Active Course",
        }
        help_texts = {
            "code": "Unique course code (e.g. CS-101, HQ-005).",
            "is_active": "Only active courses appear on student dashboards and accept new attendance records.",
        }

    def clean_code(self):
        code = self.cleaned_data.get("code", "").strip().upper()
        if not code:
            raise forms.ValidationError("Course code is required.")
        qs = Course.objects.filter(code__iexact=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f"A course with code '{code}' already exists.")
        return code

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Course name is required.")
        return name

    def clean_semester(self):
        semester = self.cleaned_data.get("semester", "").strip()
        if not semester:
            raise forms.ValidationError("Semester is required.")
        return semester


class AnnouncementForm(forms.ModelForm):
    course = forms.ModelChoiceField(
        queryset=Course.objects.filter(is_active=True).order_by("code"),
        required=False,
        empty_label="General (Class-Wide Notice)",
        label="Target Course",
        help_text="Select a course to make this notice course-specific, or leave blank for a general class-wide announcement.",
    )
    is_pinned = forms.BooleanField(required=False, initial=False, label="Pin to top")
    is_published = forms.BooleanField(required=False, initial=True, label="Publish immediately")

    class Meta:
        model = Announcement
        fields = ["title", "message", "course", "is_pinned", "is_published"]
        labels = {
            "title": "Announcement Title",
            "message": "Message Content",
            "course": "Target Course",
            "is_pinned": "Pin to top",
            "is_published": "Publish immediately",
        }
        widgets = {
            "title": forms.TextInput(attrs={
                "placeholder": "e.g. Midterm Examination Schedule Fall 2026",
                "maxlength": "200",
            }),
            "message": forms.Textarea(attrs={
                "rows": 6,
                "placeholder": "Write the detailed announcement message here...",
            }),
        }

    def clean_title(self):
        title = self.cleaned_data.get("title", "").strip()
        if not title:
            raise forms.ValidationError("Title is required.")
        return title

    def clean_message(self):
        message = self.cleaned_data.get("message", "").strip()
        if not message:
            raise forms.ValidationError("Message content is required.")
        return message

    def clean_is_published(self):
        if "_has_published" in self.data:
            return bool(self.cleaned_data.get("is_published"))
        if "is_published" in self.data:
            return bool(self.cleaned_data.get("is_published"))
        return self.instance.is_published if self.instance.pk else True

    def clean_is_pinned(self):
        if "_has_pinned" in self.data:
            return bool(self.cleaned_data.get("is_pinned"))
        if "is_pinned" in self.data:
            return bool(self.cleaned_data.get("is_pinned"))
        return self.instance.is_pinned if self.instance.pk else False



class AttendanceEntryForm(forms.Form):
    date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"})
    )
    students = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

class AttendanceImportForm(forms.Form):
    course = forms.ModelChoiceField(
        queryset=None,
        label="Select course",
    )
    file = forms.FileField(
        label="Attendance Excel file (.xlsx)",
    )

    def __init__(self, *args, **kwargs):
        from .models import Course

        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(
            is_active=True
        ).order_by("code")

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]

        if not uploaded_file.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Please upload an .xlsx file.")

        if uploaded_file.size > 5 * 1024 * 1024:
            raise forms.ValidationError("The file must be smaller than 5 MB.")

        return uploaded_file

class EnrollmentImportForm(forms.Form):
    course = forms.ModelChoiceField(
        queryset=None,
        label="Select course",
    )
    file = forms.FileField(
        label="Student roster Excel file (.xlsx)",
    )

    def __init__(self, *args, **kwargs):
        from .models import Course

        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(
            is_active=True
        ).order_by("code")

    def clean_file(self):
        uploaded_file = self.cleaned_data["file"]

        if not uploaded_file.name.lower().endswith(".xlsx"):
            raise forms.ValidationError(
                "Please upload an .xlsx file."
            )

        if uploaded_file.size > 5 * 1024 * 1024:
            raise forms.ValidationError(
                "The file must be smaller than 5 MB."
            )

        return uploaded_file


class SubmissionTaskForm(forms.ModelForm):
    course = forms.ModelChoiceField(
        queryset=None,
        label="Course",
    )

    class Meta:
        model = SubmissionTask
        fields = [
            "course",
            "title",
            "instructions",
            "opens_at",
            "deadline",
            "deadline_policy",
            "submission_type",
            "max_file_size_mb",
            "allowed_extensions",
        ]
        labels = {
            "title": "Task Title",
            "instructions": "Instructions",
            "opens_at": "Opens At",
            "deadline": "Deadline",
            "deadline_policy": "Deadline Policy",
            "submission_type": "Submission Type",
            "max_file_size_mb": "Max File Size (MB)",
            "allowed_extensions": "Allowed Extensions",
        }
        widgets = {
            "opens_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "deadline": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "instructions": forms.Textarea(
                attrs={"rows": 4, "placeholder": "Enter task instructions, guidelines, rubrics, etc."}
            ),
            "title": forms.TextInput(
                attrs={"placeholder": "e.g. Assignment 1: Recursion & Trees"}
            ),
            "allowed_extensions": forms.TextInput(
                attrs={"placeholder": ".pdf,.zip,.py,.txt,.docx"}
            ),
        }
        help_texts = {
            "deadline_policy": "Strict blocks submissions after deadline. Allow late marks submissions received after deadline as 'Late'.",
            "allowed_extensions": "Comma-separated list (e.g. .pdf,.zip,.py). Leave blank to allow any extension.",
            "max_file_size_mb": "Maximum upload size in megabytes (default 10 MB).",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(
            is_active=True
        ).order_by("code")

    def clean_title(self):
        title = self.cleaned_data.get("title", "").strip()
        if not title:
            raise forms.ValidationError("Task title is required.")
        return title

    def clean_max_file_size_mb(self):
        size = self.cleaned_data.get("max_file_size_mb")
        if size is None or size < 1:
            raise forms.ValidationError("Max file size must be at least 1 MB.")
        if size > 100:
            raise forms.ValidationError("Max file size cannot exceed 100 MB.")
        return size

    def clean_allowed_extensions(self):
        exts = self.cleaned_data.get("allowed_extensions", "").strip()
        if not exts:
            return ""
        cleaned_list = []
        for item in exts.split(","):
            item = item.strip().lower()
            if not item:
                continue
            if not item.startswith("."):
                item = f".{item}"
            if item not in cleaned_list:
                cleaned_list.append(item)
        return ",".join(cleaned_list)

    def clean(self):
        cleaned_data = super().clean()
        opens_at = cleaned_data.get("opens_at")
        deadline = cleaned_data.get("deadline")
        if opens_at and deadline:
            if deadline <= opens_at:
                self.add_error("deadline", "The deadline must be strictly after the opening time.")
        return cleaned_data


class StudentSubmissionForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = ["file", "text_answer"]
        labels = {
            "file": "Upload File",
            "text_answer": "Text Answer / Notes",
        }
        widgets = {
            "text_answer": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Type or paste your text answer or additional notes here...",
                }
            ),
        }

    def __init__(self, *args, task=None, **kwargs):
        self.task = task
        super().__init__(*args, **kwargs)
        if self.task:
            if self.task.submission_type == SubmissionTask.SubmissionType.FILE:
                self.fields["text_answer"].widget = forms.HiddenInput()
                self.fields["text_answer"].required = False
                self.fields["file"].help_text = (
                    f"Max size: {self.task.max_file_size_mb} MB. "
                    f"Allowed extensions: {self.task.allowed_extensions or 'Any'}."
                )
            elif self.task.submission_type == SubmissionTask.SubmissionType.TEXT:
                self.fields["file"].widget = forms.HiddenInput()
                self.fields["file"].required = False
            else:
                self.fields["file"].help_text = (
                    f"Optional or required depending on your answer. Max: {self.task.max_file_size_mb} MB. "
                    f"Allowed: {self.task.allowed_extensions or 'Any'}."
                )

    def clean(self):
        cleaned_data = super().clean()
        file = cleaned_data.get("file")
        text_answer = cleaned_data.get("text_answer", "").strip()

        has_existing_file = bool(self.instance and self.instance.pk and self.instance.file)
        has_existing_text = bool(self.instance and self.instance.pk and self.instance.text_answer)

        if not self.task:
            return cleaned_data

        if self.task.submission_type == SubmissionTask.SubmissionType.FILE:
            if not file and not has_existing_file:
                self.add_error("file", "Please select a file to upload.")
        elif self.task.submission_type == SubmissionTask.SubmissionType.TEXT:
            if not text_answer and not has_existing_text:
                self.add_error("text_answer", "Please provide a text answer.")
        else:
            if not file and not text_answer and not has_existing_file and not has_existing_text:
                raise forms.ValidationError("Please provide a file upload, a text answer, or both.")

        if file:
            max_bytes = self.task.max_file_size_mb * 1024 * 1024
            if file.size > max_bytes:
                self.add_error(
                    "file",
                    f"File exceeds maximum allowed size of {self.task.max_file_size_mb} MB (uploaded: {file.size / (1024 * 1024):.1f} MB)."
                )

            import os
            _, ext = os.path.splitext(file.name.lower())

            # Global blocklist for dangerous/executable/web-script extensions
            dangerous_extensions = {
                ".exe", ".bat", ".cmd", ".sh", ".com", ".msi", ".php",
                ".phtml", ".html", ".htm", ".svg", ".js", ".vbs", ".scr", ".jar"
            }
            if ext in dangerous_extensions:
                self.add_error(
                    "file",
                    f"File extension '{ext}' is prohibited for security reasons."
                )

            allowed_exts = self.task.get_allowed_extensions_list()
            if allowed_exts:
                if ext not in allowed_exts:
                    self.add_error(
                        "file",
                        f"File extension '{ext}' is not allowed. Permitted: {', '.join(allowed_exts)}."
                    )

        return cleaned_data


class PollForm(forms.ModelForm):
    course = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Course",
        empty_label="All Students (General Class Poll)",
        help_text="Select a specific course, or leave as General Class Poll.",
    )
    options_text = forms.CharField(
        label="Poll Options",
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "placeholder": "Option 1\nOption 2\nOption 3 (optional)\nOption 4 (optional)",
            }
        ),
        help_text="Enter at least 2 distinct options, one option per line.",
    )

    class Meta:
        model = Poll
        fields = [
            "course",
            "question",
            "description",
            "opens_at",
            "closes_at",
            "results_visibility",
        ]
        labels = {
            "question": "Poll Question",
            "description": "Description / Details",
            "opens_at": "Opens At",
            "closes_at": "Closes At",
            "results_visibility": "Results Visibility",
        }
        widgets = {
            "opens_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "closes_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "question": forms.TextInput(
                attrs={"placeholder": "e.g. When should we hold the review session?"}
            ),
            "description": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Additional context or notes for students (optional)..."}
            ),
        }
        help_texts = {
            "results_visibility": "'Visible after voting' allows students to see live percentages once they vote. 'Visible only after poll closes' hides results until the poll closes.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = Course.objects.filter(
            is_active=True
        ).order_by("code")
        if self.instance and self.instance.pk:
            opts = self.instance.options.all().values_list("text", flat=True)
            if opts:
                self.fields["options_text"].initial = "\n".join(opts)

    def clean_question(self):
        question = self.cleaned_data.get("question", "").strip()
        if not question:
            raise forms.ValidationError("Poll question is required.")
        return question

    def clean_options_text(self):
        raw = self.cleaned_data.get("options_text", "")
        options = []
        for line in raw.splitlines():
            line = line.strip()
            if line and line not in options:
                options.append(line)
        if len(options) < 2:
            raise forms.ValidationError("A poll must have at least 2 distinct options.")
        return options

    def clean(self):
        cleaned_data = super().clean()
        opens_at = cleaned_data.get("opens_at")
        closes_at = cleaned_data.get("closes_at")
        if opens_at and closes_at:
            if closes_at <= opens_at:
                self.add_error("closes_at", "The closing time must be strictly after the opening time.")
        return cleaned_data


class PollVoteForm(forms.Form):
    option = forms.ModelChoiceField(
        queryset=PollOption.objects.none(),
        widget=forms.RadioSelect(attrs={"class": "poll-radio"}),
        empty_label=None,
        label="Select an option",
    )

    def __init__(self, *args, poll=None, **kwargs):
        super().__init__(*args, **kwargs)
        if poll:
            self.fields["option"].queryset = poll.options.all()


RESOURCE_ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".txt", ".rtf", ".csv", ".zip", ".rar", ".7z", ".tar", ".gz",
    ".png", ".jpg", ".jpeg"
}
RESOURCE_DANGEROUS_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".sh", ".com", ".msi", ".php",
    ".phtml", ".html", ".htm", ".svg", ".js", ".vbs", ".scr", ".jar", ".py"
}
RESOURCE_MAX_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB


class CourseResourceForm(forms.ModelForm):
    is_published = forms.BooleanField(required=False, initial=True, label="Publish immediately")

    class Meta:
        model = CourseResource
        fields = ["title", "category", "description", "file", "is_published"]
        labels = {
            "title": "Resource Title",
            "category": "Category",
            "description": "Description / Notes",
            "file": "File Upload (Max 25 MB)",
            "is_published": "Publish immediately",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Chapter 4 - Normalization & ER Diagrams"}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional description or study guidance..."}),
            "file": forms.FileInput(attrs={"accept": ".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.txt,.rtf,.csv,.zip,.rar,.7z,.tar,.gz,.png,.jpg,.jpeg"}),
        }

    def clean_title(self):
        title = self.cleaned_data.get("title", "").strip()
        if not title:
            raise forms.ValidationError("Resource title is required.")
        return title

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if not file:
            raise forms.ValidationError("Please select a file to upload.")

        if file.size > RESOURCE_MAX_SIZE_BYTES:
            raise forms.ValidationError(
                f"File exceeds maximum allowed size of 25 MB (uploaded: {file.size / (1024 * 1024):.1f} MB)."
            )

        _, ext = os.path.splitext(file.name.lower())
        if ext in RESOURCE_DANGEROUS_EXTENSIONS or ext not in RESOURCE_ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"File extension '{ext}' is not permitted. Allowed formats include PDF, DOCX, PPTX, XLSX, TXT, images, and archives (ZIP/RAR)."
            )
        return file

    def clean_is_published(self):
        if "_has_published" in self.data:
            return bool(self.cleaned_data.get("is_published"))
        if "is_published" in self.data:
            return bool(self.cleaned_data.get("is_published"))
        return True


class CourseResourceEditForm(forms.ModelForm):
    is_published = forms.BooleanField(required=False, initial=True, label="Publish immediately")
    file = forms.FileField(
        required=False,
        label="Replace File (Optional, Max 25 MB)",
        help_text="Leave empty to keep the existing file.",
        widget=forms.FileInput(attrs={"accept": ".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.txt,.rtf,.csv,.zip,.rar,.7z,.tar,.gz,.png,.jpg,.jpeg"}),
    )

    class Meta:
        model = CourseResource
        fields = ["title", "category", "description", "file", "is_published"]
        labels = {
            "title": "Resource Title",
            "category": "Category",
            "description": "Description / Notes",
            "file": "Replace File",
            "is_published": "Publish immediately",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Chapter 4 - Normalization & ER Diagrams"}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional description or study guidance..."}),
        }

    def clean_title(self):
        title = self.cleaned_data.get("title", "").strip()
        if not title:
            raise forms.ValidationError("Resource title is required.")
        return title

    def clean_file(self):
        file = self.cleaned_data.get("file")
        if not file:
            return None

        if file.size > RESOURCE_MAX_SIZE_BYTES:
            raise forms.ValidationError(
                f"File exceeds maximum allowed size of 25 MB (uploaded: {file.size / (1024 * 1024):.1f} MB)."
            )

        _, ext = os.path.splitext(file.name.lower())
        if ext in RESOURCE_DANGEROUS_EXTENSIONS or ext not in RESOURCE_ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                f"File extension '{ext}' is not permitted. Allowed formats include PDF, DOCX, PPTX, XLSX, TXT, images, and archives (ZIP/RAR)."
            )
        return file

    def clean_is_published(self):
        if "_has_published" in self.data:
            return bool(self.cleaned_data.get("is_published"))
        if "is_published" in self.data:
            return bool(self.cleaned_data.get("is_published"))
        return self.instance.is_published if self.instance.pk else True


class CourseLinkForm(forms.ModelForm):
    is_published = forms.BooleanField(required=False, initial=True, label="Publish immediately")

    class Meta:
        model = CourseLink
        fields = ["title", "category", "url", "description", "is_published"]
        labels = {
            "title": "Link Title",
            "category": "Category",
            "url": "Web URL",
            "description": "Description / Notes",
            "is_published": "Publish immediately",
        }
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "e.g. Official WhatsApp Group or Reference Documentation"}),
            "url": forms.URLInput(attrs={"placeholder": "https://chat.whatsapp.com/... or https://..."}),
            "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Optional notes about this link..."}),
        }

    def clean_title(self):
        title = self.cleaned_data.get("title", "").strip()
        if not title:
            raise forms.ValidationError("Link title is required.")
        return title

    def clean_url(self):
        url = self.cleaned_data.get("url", "").strip()
        if not url:
            raise forms.ValidationError("URL is required.")

        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            raise forms.ValidationError("URL must start with http:// or https://.")
        if not parsed.netloc:
            raise forms.ValidationError("Please provide a valid web address.")
        return url

    def clean_is_published(self):
        if "_has_published" in self.data:
            return bool(self.cleaned_data.get("is_published"))
        if "is_published" in self.data:
            return bool(self.cleaned_data.get("is_published"))
        return self.instance.is_published if self.instance.pk else True
