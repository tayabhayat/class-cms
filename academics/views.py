
import io
import os
import re
import zipfile
from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .attendance_import import parse_attendance_excel
from .enrollment_import import parse_enrollment_excel
from .forms import (
    AnnouncementForm,
    AttendanceEntryForm,
    AttendanceImportForm,
    CourseForm,
    EnrollmentImportForm,
    PollForm,
    PollVoteForm,
    StudentSubmissionForm,
    SubmissionTaskForm,
)
from .models import (
    Announcement,
    Attendance,
    AttendanceSession,
    Course,
    Enrollment,
    Poll,
    PollOption,
    PollVote,
    Submission,
    SubmissionTask,
)

import tempfile

User = get_user_model()


def sanitize_filename(filename: str) -> str:
    """
    Sanitizes filenames to prevent directory traversal (POSIX and Windows),
    CRLF injection, header splitting, and dangerous control characters.
    """
    if not filename:
        return "submission"
    # Normalize both Windows and Unix path separators
    clean = str(filename).replace("\\", "/").split("/")[-1]
    # Remove control characters, newlines, carriage returns, quotes, and metacharacters
    clean = re.sub(r'[\x00-\x1f\x7f-\x9f"\'<>:|?*]', "", clean)
    # Strip leading/trailing dots and whitespace
    clean = clean.strip(". ")
    if not clean:
        clean = "submission"
    if len(clean) > 200:
        base, ext = os.path.splitext(clean)
        clean = base[: 200 - len(ext)] + ext
    return clean


@login_required
def dashboard(request):
    if request.user.role == "student":
        enrollments = (
            Enrollment.objects
            .filter(student=request.user, course__is_active=True)
            .select_related("course")
        )
        courses = [enrollment.course for enrollment in enrollments]
    else:
        courses = Course.objects.filter(is_active=True)

    announcements = Announcement.objects.order_by("-created_at")[:5]

    return render(
        request,
        "academics/dashboard.html",
        {"courses": courses, "announcements": announcements},
    )


@login_required
def course_detail(request, course_id):
    if request.user.role == "student":
        enrollment = get_object_or_404(
            Enrollment.objects.select_related("course"),
            student=request.user,
            course_id=course_id,
            course__is_active=True,
        )
        course = enrollment.course
    else:
        course = get_object_or_404(Course, id=course_id)

    return render(
        request,
        "academics/course_detail.html",
        {"course": course},
    )

def is_admin(user):
    return user.is_authenticated and user.role == "admin"


@login_required
@user_passes_test(is_admin)
def mark_attendance(request, course_id):
    course = get_object_or_404(Course, id=course_id)

    if not course.is_active:
        if request.method == "POST":
            messages.error(
                request,
                f"Cannot mark attendance for {course.code} because the course is inactive. Please activate it first."
            )
            return redirect("course_detail", course_id=course.id)
        else:
            messages.warning(
                request,
                f"{course.code} is currently inactive. You must activate this course to record new attendance sessions."
            )

    students = User.objects.filter(
        role="student",
        enrollments__course=course,
    ).order_by("student_id")

    allowed_ids = set(students.values_list("id", flat=True))

    if request.method == "POST":
        form = AttendanceEntryForm(request.POST)

        if form.is_valid():
            session_date = form.cleaned_data["date"]
            submitted = {}

            # Validate that no foreign or invalid student IDs are submitted
            for key in request.POST:
                if key.startswith("status_"):
                    raw_id = key[len("status_"):]
                    try:
                        s_id = int(raw_id)
                    except ValueError:
                        form.add_error(None, f"Invalid student identifier: {raw_id}.")
                        continue
                    if s_id not in allowed_ids:
                        form.add_error(
                            None,
                            f"Student with ID {s_id} is not enrolled in {course.code}."
                        )

            for student in students:
                status = request.POST.get(f"status_{student.id}")
                if status not in {"present", "absent", "leave"}:
                    form.add_error(
                        None,
                        f"Select a valid status for {student.student_id or student.email}."
                    )
                    break
                submitted[student.id] = status

            if (
                not form.non_field_errors()
                and set(submitted) == allowed_ids
            ):
                with transaction.atomic():
                    session, _ = AttendanceSession.objects.get_or_create(
                        course=course,
                        date=session_date,
                        defaults={"created_by": request.user},
                    )

                    for student_id, status in submitted.items():
                        Attendance.objects.update_or_create(
                            session=session,
                            student_id=student_id,
                            defaults={"status": status},
                        )

                messages.success(
                    request,
                    f"Attendance saved successfully for {session_date}."
                )
                return redirect(
                    f"{reverse('mark_attendance', kwargs={'course_id': course.id})}?date={session_date}"
                )
    else:
        selected_date = None
        if "date" in request.GET:
            try:
                selected_date = date.fromisoformat(request.GET["date"])
            except (ValueError, TypeError):
                selected_date = None

        if selected_date is None:
            selected_date = timezone.localdate()

        form = AttendanceEntryForm(initial={"date": selected_date})

    if form.is_bound and form.is_valid():
        session_date = form.cleaned_data["date"]
    elif "date" in request.GET:
        try:
            session_date = date.fromisoformat(request.GET["date"])
        except (ValueError, TypeError):
            session_date = timezone.localdate()
    else:
        session_date = timezone.localdate()

    existing = {}
    session = AttendanceSession.objects.filter(
        course=course, date=session_date
    ).first()

    if session:
        existing = {
            record.student_id: record.status
            for record in Attendance.objects.filter(session=session)
        }

    for student in students:
        student.current_status = existing.get(student.id, "")

    return render(
        request,
        "academics/mark_attendance.html",
        {
            "course": course,
            "students": students,
            "form": form,
            "existing": existing,
            "session_date": session_date,
        },
    )


@login_required
@user_passes_test(is_admin)
def view_course_attendance(request, course_id):
    """
    Renders the full course attendance register for Admin/CR users.
    Displays all enrolled students (including those without attendance records),
    session-by-session status, and student-level totals and percentages.
    """
    course = get_object_or_404(Course, id=course_id)

    # 1. Fetch all enrolled students with user details in a single query
    enrollments = (
        Enrollment.objects.filter(course=course)
        .select_related("student")
        .order_by("student__student_id", "student__first_name", "student__email")
    )

    # 2. Fetch all attendance sessions for this course in chronological order
    sessions = list(AttendanceSession.objects.filter(course=course).order_by("date", "id"))

    # 3. Fetch all attendance records for these sessions in one query
    records = Attendance.objects.filter(session__in=sessions).values(
        "session_id", "student_id", "status"
    )

    # Fast in-memory lookup map: (session_id, student_id) -> status
    attendance_map = {
        (r["session_id"], r["student_id"]): r["status"]
        for r in records
    }

    total_sessions_count = len(sessions)
    student_rows = []
    total_class_present = 0
    total_class_recorded = 0

    for enrollment in enrollments:
        student = enrollment.student
        student_sessions = []
        present_count = 0
        absent_count = 0
        leave_count = 0
        recorded_count = 0

        for session in sessions:
            status = attendance_map.get((session.id, student.id))
            if status == Attendance.Status.PRESENT:
                present_count += 1
                recorded_count += 1
            elif status == Attendance.Status.ABSENT:
                absent_count += 1
                recorded_count += 1
            elif status == Attendance.Status.LEAVE:
                leave_count += 1
                recorded_count += 1

            student_sessions.append({
                "session": session,
                "status": status,
            })

        total_class_present += present_count
        total_class_recorded += recorded_count

        if total_sessions_count > 0:
            percentage = round((present_count / total_sessions_count) * 100, 1)
        else:
            percentage = 0.0

        student_rows.append({
            "student": student,
            "student_id": student.student_id or "—",
            "name": student.get_full_name() or student.email,
            "email": student.email,
            "sessions": student_sessions,
            "present_count": present_count,
            "absent_count": absent_count,
            "leave_count": leave_count,
            "recorded_count": recorded_count,
            "has_records": recorded_count > 0,
            "percentage": percentage,
        })

    # Class-level overall attendance percentage across all enrolled student-sessions
    if total_sessions_count > 0 and len(student_rows) > 0:
        total_possible = total_sessions_count * len(student_rows)
        class_average_percentage = round((total_class_present / total_possible) * 100, 1)
    else:
        class_average_percentage = 0.0

    return render(
        request,
        "academics/course_attendance.html",
        {
            "course": course,
            "sessions": sessions,
            "student_rows": student_rows,
            "total_students": len(student_rows),
            "total_sessions": total_sessions_count,
            "class_average_percentage": class_average_percentage,
        },
    )


@login_required
@user_passes_test(is_admin)
def import_attendance(request):
    preview = None
    errors = []
    course = None
    can_confirm = False
    skipped_count = 0

    if request.method == "POST":
        form = AttendanceImportForm(request.POST, request.FILES)

        is_confirm = request.POST.get("action") == "confirm"
        cached_file_data = request.session.get("attendance_import_cache")

        # Fallback to session cache if confirming and browser didn't re-upload file
        if is_confirm and not request.FILES.get("file") and cached_file_data:
            course_id = request.POST.get("course") or cached_file_data.get("course_id")
            file_bytes = bytes.fromhex(cached_file_data["content"])
            uploaded_file = SimpleUploadedFile(
                cached_file_data["filename"],
                file_bytes,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            form = AttendanceImportForm(
                data={"course": course_id},
                files={"file": uploaded_file},
            )

        if form.is_valid():
            course = form.cleaned_data["course"]
            uploaded_file = form.cleaned_data["file"]

            # Cache file bytes for confirmation
            file_bytes = uploaded_file.read()
            uploaded_file.seek(0)
            request.session["attendance_import_cache"] = {
                "filename": uploaded_file.name,
                "content": file_bytes.hex(),
                "course_id": course.id,
            }

            try:
                records, parser_errors = parse_attendance_excel(
                    uploaded_file
                )
            except (ValueError, OSError) as exc:
                form.add_error("file", str(exc))
                errors.append(str(exc))
                return render(
                    request,
                    "academics/attendance_import.html",
                    {
                        "form": form,
                        "course": course,
                        "preview": None,
                        "errors": errors,
                        "can_confirm": False,
                        "skipped_count": 0,
                    },
                )

            errors.extend(parser_errors)
            skipped_count = getattr(records, "skipped_count", 0)

            student_ids = {
                record["student_id"] for record in records
            }

            students_qs = User.objects.filter(
                role="student",
                student_id__in=student_ids,
            )
            students_by_id = {
                student.student_id.upper(): student
                for student in students_qs
                if student.student_id
            }

            enrolled_ids = set(
                Enrollment.objects.filter(
                    course=course,
                    student__in=students_qs,
                ).values_list("student__student_id", flat=True)
            )

            for sid in sorted(student_ids):
                if sid not in students_by_id:
                    errors.append(f"{sid}: unknown student ID.")
                elif sid not in enrolled_ids:
                    errors.append(
                        f"{sid}: student is not enrolled in {course.code}."
                    )

            record_dates = set()
            for record in records:
                try:
                    record_dates.add(date.fromisoformat(record["date"]))
                except (ValueError, TypeError):
                    errors.append(
                        f"Invalid date {record.get('date')!r} for student {record.get('student_id')}."
                    )

            existing_sessions = AttendanceSession.objects.filter(
                course=course,
                date__in=record_dates,
            )
            sessions_by_date = {s.date: s for s in existing_sessions}

            existing_records = Attendance.objects.filter(
                session__in=existing_sessions,
            ).select_related("session", "student")
            existing_attendance_map = {
                (r.session.date, r.student.student_id.upper()): r
                for r in existing_records
                if r.student.student_id
            }

            preview = {
                "new": [],
                "changed": [],
                "unchanged": [],
            }

            for record in records:
                student_id = record["student_id"].upper()
                student = students_by_id.get(student_id)

                if student is None or student_id not in enrolled_ids:
                    continue

                try:
                    session_date = date.fromisoformat(record["date"])
                except (ValueError, TypeError):
                    continue

                existing = existing_attendance_map.get(
                    (session_date, student_id)
                )

                item = {
                    **record,
                    "student_name": (
                        student.get_full_name() or student.email
                    ),
                    "existing_status": (
                        existing.get_status_display()
                        if existing else None
                    ),
                    "student": student,
                    "session_date": session_date,
                }

                if existing is None:
                    preview["new"].append(item)
                elif existing.status == record["status"]:
                    preview["unchanged"].append(item)
                else:
                    preview["changed"].append(item)

            can_confirm = bool(
                preview
                and not errors
                and (preview["new"] or preview["changed"])
            )

            if is_confirm:
                if errors:
                    request.session.pop("attendance_import_cache", None)
                    messages.error(
                        request,
                        "Attendance import cancelled. Fix spreadsheet errors, "
                        "invalid student IDs, or enrollment issues before confirming.",
                    )
                elif not (preview["new"] or preview["changed"]):
                    request.session.pop("attendance_import_cache", None)
                    messages.info(
                        request,
                        "No new or changed attendance records to import.",
                    )
                else:
                    with transaction.atomic():
                        needed_dates = {
                            item["session_date"]
                            for item in (preview["new"] + preview["changed"])
                        }
                        for s_date in needed_dates:
                            if s_date not in sessions_by_date:
                                session, _ = AttendanceSession.objects.get_or_create(
                                    course=course,
                                    date=s_date,
                                    defaults={"created_by": request.user},
                                )
                                sessions_by_date[s_date] = session

                        for item in preview["new"]:
                            session = sessions_by_date[item["session_date"]]
                            Attendance.objects.create(
                                session=session,
                                student=item["student"],
                                status=item["status"],
                            )

                        for item in preview["changed"]:
                            session = sessions_by_date[item["session_date"]]
                            Attendance.objects.filter(
                                session=session,
                                student=item["student"],
                            ).update(status=item["status"])

                    request.session.pop("attendance_import_cache", None)
                    added_count = len(preview["new"])
                    changed_count = len(preview["changed"])
                    unchanged_count = len(preview["unchanged"])

                    messages.success(
                        request,
                        f"Attendance import completed successfully. "
                        f"{added_count} added, {changed_count} changed, "
                        f"{unchanged_count} unchanged, {skipped_count} skipped.",
                    )
                    return redirect("import_attendance")

    else:
        form = AttendanceImportForm()

    return render(
        request,
        "academics/attendance_import.html",
        {
            "form": form,
            "course": course,
            "preview": preview,
            "errors": errors,
            "can_confirm": can_confirm,
            "skipped_count": skipped_count,
        },
    )

@login_required
@user_passes_test(is_admin)
def import_enrollments(request):
    if request.method == "POST":
        form = EnrollmentImportForm(request.POST, request.FILES)

        if form.is_valid():
            course = form.cleaned_data["course"]
            uploaded_file = form.cleaned_data["file"]

            try:
                student_ids, errors = parse_enrollment_excel(uploaded_file)
            except (ValueError, OSError) as exc:
                form.add_error("file", str(exc))
                return render(
                    request,
                    "academics/enrollment_import.html",
                    {"form": form},
                )

            students = User.objects.filter(
                role="student",
                student_id__in=student_ids,
            ).in_bulk(field_name="student_id")

            existing_ids = set(
                Enrollment.objects.filter(
                    course=course,
                    student__student_id__in=student_ids,
                ).values_list("student__student_id", flat=True)
            )

            preview = {
                "new_enrollments": [],
                "already_enrolled": [],
                "invalid_ids": [],
                "errors": errors,
            }

            for student_id in student_ids:
                student = students.get(student_id)

                if student is None:
                    preview["invalid_ids"].append(student_id)
                elif student_id in existing_ids:
                    preview["already_enrolled"].append(student_id)
                else:
                    preview["new_enrollments"].append({
                        "student_id": student_id,
                        "name": student.get_full_name() or student.email,
                    })

            if request.POST.get("action") == "confirm":
                if errors or preview["invalid_ids"]:
                    messages.error(
                        request,
                        "Enrollment cancelled. Fix spreadsheet errors "
                        "and invalid student IDs before confirming.",
                    )
                else:
                    new_ids = [
                        item["student_id"]
                        for item in preview["new_enrollments"]
                    ]

                    students_to_enroll = [
                        students[student_id]
                        for student_id in new_ids
                    ]

                    with transaction.atomic():
                        created = Enrollment.objects.bulk_create(
                            [
                                Enrollment(student=student, course=course)
                                for student in students_to_enroll
                            ],
                            ignore_conflicts=True,
                        )

                    messages.success(
                        request,
                        f"Enrollment import completed. "
                        f"{len(created)} enrollment(s) added. "
                        f"Existing enrollments were preserved.",
                    )
                    return redirect("import_enrollments")

            return render(
                request,
                "academics/enrollment_import.html",
                {
                    "form": form,
                    "course": course,
                    "preview": preview,
                },
            )
    else:
        form = EnrollmentImportForm()

    return render(
        request,
        "academics/enrollment_import.html",
        {"form": form},
    )


@login_required
def my_attendance(request, course_id):
    if request.user.role != "student":
        raise Http404("Only enrolled students can access this attendance summary.")

    enrollment = get_object_or_404(
        Enrollment.objects.select_related("course"),
        student=request.user,
        course_id=course_id,
        course__is_active=True,
    )
    course = enrollment.course

    sessions = AttendanceSession.objects.filter(course=course).order_by("-date")
    records = Attendance.objects.filter(
        session__in=sessions,
        student=request.user,
    ).select_related("session")
    records_by_session = {record.session_id: record for record in records}

    attendance_history = []
    present_count = 0
    absent_count = 0
    leave_count = 0
    unrecorded_count = 0

    for session in sessions:
        record = records_by_session.get(session.id)
        if record:
            status = record.status
            status_display = record.get_status_display()
            if status == Attendance.Status.PRESENT:
                present_count += 1
            elif status == Attendance.Status.ABSENT:
                absent_count += 1
            elif status == Attendance.Status.LEAVE:
                leave_count += 1
        else:
            status = "not_marked"
            status_display = "Not Marked"
            unrecorded_count += 1

        attendance_history.append({
            "session": session,
            "date": session.date,
            "status": status,
            "status_display": status_display,
        })

    total_sessions = len(attendance_history)
    if total_sessions > 0:
        percentage = round((present_count / total_sessions) * 100, 1)
    else:
        percentage = 0.0

    return render(
        request,
        "academics/my_attendance.html",
        {
            "course": course,
            "enrollment": enrollment,
            "attendance_history": attendance_history,
            "total_sessions": total_sessions,
            "present_count": present_count,
            "absent_count": absent_count,
            "leave_count": leave_count,
            "unrecorded_count": unrecorded_count,
            "percentage": percentage,
        },
    )


@login_required
@user_passes_test(is_admin)
def manage_courses(request):
    courses = (
        Course.objects
        .annotate(
            enrollment_count=Count("enrollments", distinct=True),
            session_count=Count("attendance_sessions", distinct=True),
        )
        .order_by("code")
    )
    total_courses = courses.count()
    active_courses = sum(1 for c in courses if c.is_active)
    inactive_courses = total_courses - active_courses

    return render(
        request,
        "academics/manage_courses.html",
        {
            "courses": courses,
            "total_courses": total_courses,
            "active_courses": active_courses,
            "inactive_courses": inactive_courses,
        },
    )


@login_required
@user_passes_test(is_admin)
def create_course(request):
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save()
            messages.success(
                request,
                f"Course '{course.code} - {course.name}' created successfully."
            )
            return redirect("manage_courses")
    else:
        form = CourseForm()

    return render(
        request,
        "academics/course_form.html",
        {
            "form": form,
            "title": "Add New Course",
            "is_create": True,
        },
    )


@login_required
@user_passes_test(is_admin)
def edit_course(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    if request.method == "POST":
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            course = form.save()
            messages.success(
                request,
                f"Course '{course.code}' updated successfully."
            )
            return redirect("manage_courses")
    else:
        form = CourseForm(instance=course)

    return render(
        request,
        "academics/course_form.html",
        {
            "form": form,
            "course": course,
            "title": f"Edit Course: {course.code}",
            "is_create": False,
        },
    )


@login_required
@user_passes_test(is_admin)
def toggle_course_status(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    if request.method == "POST":
        course.is_active = not course.is_active
        course.save(update_fields=["is_active"])
        action = "activated" if course.is_active else "deactivated"
        messages.success(
            request,
            f"Course '{course.code}' has been {action}."
        )
        return redirect("manage_courses")

    enrollment_count = course.enrollments.count()
    session_count = course.attendance_sessions.count()

    return render(
        request,
        "academics/course_confirm_toggle.html",
        {
            "course": course,
            "enrollment_count": enrollment_count,
            "session_count": session_count,
        },
    )


@login_required
def announcement_list(request):
    announcements = (
        Announcement.objects
        .select_related("created_by")
        .order_by("-created_at")
    )
    return render(
        request,
        "academics/announcement_list.html",
        {"announcements": announcements},
    )


@login_required
@user_passes_test(is_admin)
def create_announcement(request):
    if request.method == "POST":
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            announcement = form.save(commit=False)
            announcement.created_by = request.user
            announcement.save()
            messages.success(
                request,
                f"Announcement '{announcement.title}' posted successfully."
            )
            return redirect("announcement_list")
    else:
        form = AnnouncementForm()

    return render(
        request,
        "academics/announcement_form.html",
        {
            "form": form,
            "title": "Post New Announcement",
            "is_create": True,
        },
    )


@login_required
@user_passes_test(is_admin)
def edit_announcement(request, announcement_id):
    announcement = get_object_or_404(Announcement, id=announcement_id)
    if request.method == "POST":
        form = AnnouncementForm(request.POST, instance=announcement)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                f"Announcement '{announcement.title}' updated successfully."
            )
            return redirect("announcement_list")
    else:
        form = AnnouncementForm(instance=announcement)

    return render(
        request,
        "academics/announcement_form.html",
        {
            "form": form,
            "announcement": announcement,
            "title": "Edit Announcement",
            "is_create": False,
        },
    )


@login_required
@user_passes_test(is_admin)
def delete_announcement(request, announcement_id):
    announcement = get_object_or_404(Announcement, id=announcement_id)
    if request.method == "POST":
        title = announcement.title
        announcement.delete()
        messages.success(
            request,
            f"Announcement '{title}' deleted successfully."
        )
        return redirect("announcement_list")

    return render(
        request,
        "academics/announcement_confirm_delete.html",
        {"announcement": announcement},
    )


# ---------------------------------------------------------------------------
# Task & Submission Views
# ---------------------------------------------------------------------------

@login_required
def task_list(request):
    selected_course_id = request.GET.get("course", "").strip()

    if request.user.role == "admin":
        tasks_qs = SubmissionTask.objects.select_related("course", "created_by").order_by("-deadline", "-created_at")
        courses = Course.objects.filter(is_active=True).order_by("code")
        if selected_course_id:
            tasks_qs = tasks_qs.filter(course_id=selected_course_id)

        task_data = []
        for task in tasks_qs:
            total_enrolled = Enrollment.objects.filter(course=task.course).count()
            submitted_count = task.submissions.count()
            late_count = task.submissions.filter(is_late=True).count()
            missing_count = max(0, total_enrolled - submitted_count)
            on_time_count = submitted_count - late_count
            task_data.append({
                "task": task,
                "total_enrolled": total_enrolled,
                "submitted_count": submitted_count,
                "late_count": late_count,
                "missing_count": missing_count,
                "on_time_count": on_time_count,
            })

        return render(
            request,
            "academics/task_list.html",
            {
                "task_data": task_data,
                "courses": courses,
                "selected_course_id": selected_course_id,
            },
        )
    else:
        enrolled_courses = Course.objects.filter(
            enrollments__student=request.user,
            is_active=True,
        ).order_by("code")

        tasks_qs = SubmissionTask.objects.filter(
            course__in=enrolled_courses,
        ).select_related("course").order_by("deadline")

        if selected_course_id:
            tasks_qs = tasks_qs.filter(course_id=selected_course_id)

        submissions_map = {
            s.task_id: s
            for s in Submission.objects.filter(
                student=request.user,
                task__in=tasks_qs,
            )
        }

        task_data = []
        for task in tasks_qs:
            sub = submissions_map.get(task.id)
            if sub:
                if sub.is_late:
                    status_label = "Submitted (Late)"
                    status_class = "badge-warning"
                else:
                    status_label = "Submitted"
                    status_class = "badge-success"
            else:
                if not task.is_open():
                    status_label = "Upcoming"
                    status_class = "badge-secondary"
                elif task.is_past_deadline() and task.deadline_policy == SubmissionTask.DeadlinePolicy.STRICT:
                    status_label = "Closed (Missing)"
                    status_class = "badge-danger"
                elif task.is_past_deadline() and task.deadline_policy == SubmissionTask.DeadlinePolicy.ALLOW_LATE:
                    status_label = "Missing (Late Allowed)"
                    status_class = "badge-warning"
                else:
                    status_label = "Pending"
                    status_class = "badge-info"

            task_data.append({
                "task": task,
                "submission": sub,
                "status_label": status_label,
                "status_class": status_class,
                "can_submit": task.can_submit(),
            })

        return render(
            request,
            "academics/task_list.html",
            {
                "task_data": task_data,
                "courses": enrolled_courses,
                "selected_course_id": selected_course_id,
            },
        )


@login_required
@user_passes_test(is_admin)
def create_task(request):
    if request.method == "POST":
        form = SubmissionTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.created_by = request.user
            task.save()
            messages.success(request, f"Task '{task.title}' created successfully.")
            return redirect("task_list")
    else:
        initial = {}
        course_id = request.GET.get("course")
        if course_id:
            initial["course"] = course_id
        form = SubmissionTaskForm(initial=initial)

    return render(
        request,
        "academics/task_form.html",
        {
            "form": form,
            "title": "Create Assignment / Task",
            "is_create": True,
        },
    )


@login_required
@user_passes_test(is_admin)
def edit_task(request, task_id):
    task = get_object_or_404(SubmissionTask, id=task_id)
    if request.method == "POST":
        form = SubmissionTaskForm(request.POST, instance=task)
        if form.is_valid():
            form.save()
            messages.success(request, f"Task '{task.title}' updated successfully.")
            return redirect("task_list")
    else:
        form = SubmissionTaskForm(instance=task)

    return render(
        request,
        "academics/task_form.html",
        {
            "form": form,
            "task": task,
            "title": "Edit Task",
            "is_create": False,
        },
    )


@login_required
def task_detail(request, task_id):
    task = get_object_or_404(
        SubmissionTask.objects.select_related("course"),
        id=task_id,
    )

    if request.user.role == "admin":
        return redirect("task_submission_report", task_id=task.id)

    # Student validation: check active enrollment
    is_enrolled = Enrollment.objects.filter(
        course=task.course,
        student=request.user,
        course__is_active=True,
    ).exists()

    if not is_enrolled:
        return HttpResponseForbidden("You are not enrolled in this course.")

    submission = Submission.objects.filter(
        task=task,
        student=request.user,
    ).first()

    can_submit = task.can_submit()

    if request.method == "POST":
        if not can_submit:
            if not task.is_open():
                messages.error(request, "This task is not open for submissions yet.")
            else:
                messages.error(request, "The deadline has passed and late submissions are not permitted.")
            return redirect("task_detail", task_id=task.id)

        form = StudentSubmissionForm(
            request.POST,
            request.FILES,
            instance=submission,
            task=task,
        )
        if form.is_valid():
            sub = form.save(commit=False)
            sub.task = task
            sub.student = request.user
            sub.is_late = task.is_past_deadline()
            if "file" in request.FILES:
                uploaded_file = request.FILES["file"]
                sub.original_filename = sanitize_filename(uploaded_file.name)
                sub.file = uploaded_file
            sub.save()
            if submission:
                messages.success(request, "Your submission has been updated successfully.")
            else:
                messages.success(request, "Your submission has been uploaded successfully.")
            return redirect("task_detail", task_id=task.id)
    else:
        form = StudentSubmissionForm(instance=submission, task=task)

    return render(
        request,
        "academics/task_detail.html",
        {
            "task": task,
            "submission": submission,
            "form": form,
            "can_submit": can_submit,
        },
    )


@login_required
@user_passes_test(is_admin)
def task_submission_report(request, task_id):
    task = get_object_or_404(
        SubmissionTask.objects.select_related("course"),
        id=task_id,
    )

    enrollments = (
        Enrollment.objects
        .filter(course=task.course)
        .select_related("student")
        .order_by("student__student_id", "student__first_name")
    )

    submissions = {
        s.student_id: s
        for s in Submission.objects.filter(task=task).select_related("student")
    }

    roster = []
    submitted_count = 0
    late_count = 0

    for enr in enrollments:
        student = enr.student
        sub = submissions.get(student.id)
        if sub:
            submitted_count += 1
            if sub.is_late:
                late_count += 1
                status = "Late"
                badge = "badge-warning"
            else:
                status = "Submitted"
                badge = "badge-success"
        else:
            status = "Missing"
            badge = "badge-danger"

        roster.append({
            "student": student,
            "submission": sub,
            "status": status,
            "badge": badge,
        })

    total_enrolled = len(enrollments)
    missing_count = max(0, total_enrolled - submitted_count)
    on_time_count = submitted_count - late_count

    return render(
        request,
        "academics/task_submission_report.html",
        {
            "task": task,
            "roster": roster,
            "total_enrolled": total_enrolled,
            "submitted_count": submitted_count,
            "missing_count": missing_count,
            "late_count": late_count,
            "on_time_count": on_time_count,
        },
    )


@login_required
def download_submission(request, submission_id):
    submission = get_object_or_404(
        Submission.objects.select_related("task__course", "student"),
        id=submission_id,
    )

    # Permission check: admin or student who owns the submission
    if request.user.role != "admin" and submission.student_id != request.user.id:
        return HttpResponseForbidden("You do not have permission to download this file.")

    if not submission.file:
        raise Http404("No file attached to this submission.")

    try:
        file_handle = submission.file.open("rb")
        raw_name = submission.original_filename or os.path.basename(submission.file.name)
        safe_filename = sanitize_filename(raw_name)
        return FileResponse(file_handle, as_attachment=True, filename=safe_filename)
    except (FileNotFoundError, ValueError):
        raise Http404("File not found on server.")


@login_required
@user_passes_test(is_admin)
def download_task_zip(request, task_id):
    task = get_object_or_404(
        SubmissionTask.objects.select_related("course"),
        id=task_id,
    )

    submissions = Submission.objects.filter(task=task).select_related("student")

    # Use a SpooledTemporaryFile to stream the archive without unbounded RAM usage (DoS protection)
    spooled_zip = tempfile.SpooledTemporaryFile(max_size=20 * 1024 * 1024, mode="w+b")
    with zipfile.ZipFile(spooled_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for sub in submissions:
            ident = sub.student.student_id or f"student_{sub.student.id}"
            safe_ident = re.sub(r"[^A-Za-z0-9_-]", "_", ident)

            if sub.file:
                try:
                    raw_orig = sub.original_filename or os.path.basename(sub.file.name)
                    safe_orig = re.sub(r"[^A-Za-z0-9_.-]", "_", sanitize_filename(raw_orig)).lstrip(".") or "submission.bin"
                    archive_name = f"{safe_ident}_{safe_orig}"
                    if hasattr(sub.file, "path") and os.path.isfile(sub.file.path):
                        zf.write(sub.file.path, arcname=archive_name)
                    else:
                        sub.file.open("rb")
                        zf.writestr(archive_name, sub.file.read())
                        sub.file.close()
                except Exception:
                    pass

            if sub.text_answer and sub.text_answer.strip():
                txt_name = f"{safe_ident}_text_answer.txt"
                content = (
                    f"Student: {sub.student.get_full_name()} ({ident})\n"
                    f"Email: {sub.student.email}\n"
                    f"Task: {task.title} ({task.course.code})\n"
                    f"Uploaded At: {sub.uploaded_at}\n"
                    f"Status: {'Late' if sub.is_late else 'On time'}\n\n"
                    f"--- Text Response ---\n\n"
                    f"{sub.text_answer}\n"
                )
                zf.writestr(txt_name, content.encode("utf-8"))

    spooled_zip.seek(0)
    safe_code = re.sub(r"[^A-Za-z0-9_-]", "_", task.course.code)
    zip_filename = f"{safe_code}_task_{task.id}_submissions.zip"
    response = HttpResponse(spooled_zip.read(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{zip_filename}"'
    spooled_zip.close()
    return response


# ---------------------------------------------------------------------------
# Class Poll Views
# ---------------------------------------------------------------------------

@login_required
def poll_list(request):
    selected_course_id = request.GET.get("course", "").strip()

    if request.user.role == "admin":
        polls_qs = Poll.objects.select_related("course", "created_by").order_by("-created_at")
        courses = Course.objects.filter(is_active=True).order_by("code")
        if selected_course_id:
            polls_qs = polls_qs.filter(course_id=selected_course_id)

        poll_data = []
        for poll in polls_qs:
            poll_data.append({
                "poll": poll,
                "is_active": poll.is_active(),
                "is_expired": poll.is_expired(),
                "total_votes": poll.total_votes,
            })

        return render(
            request,
            "academics/poll_list.html",
            {
                "poll_data": poll_data,
                "courses": courses,
                "selected_course_id": selected_course_id,
            },
        )
    else:
        enrolled_courses = Course.objects.filter(
            enrollments__student=request.user,
            is_active=True,
        ).order_by("code")

        polls_qs = Poll.objects.filter(
            Q(course__in=enrolled_courses) | Q(course__isnull=True),
        ).select_related("course").order_by("-created_at")

        if selected_course_id:
            polls_qs = polls_qs.filter(course_id=selected_course_id)

        voted_poll_ids = set(
            PollVote.objects.filter(
                student=request.user,
                poll__in=polls_qs,
            ).values_list("poll_id", flat=True)
        )

        poll_data = []
        for poll in polls_qs:
            has_voted = poll.id in voted_poll_ids
            poll_data.append({
                "poll": poll,
                "is_active": poll.is_active(),
                "is_expired": poll.is_expired(),
                "has_voted": has_voted,
                "can_view_results": poll.can_user_view_results(request.user),
            })

        return render(
            request,
            "academics/poll_list.html",
            {
                "poll_data": poll_data,
                "courses": enrolled_courses,
                "selected_course_id": selected_course_id,
            },
        )


@login_required
@user_passes_test(is_admin)
def create_poll(request):
    if request.method == "POST":
        form = PollForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                poll = form.save(commit=False)
                poll.created_by = request.user
                poll.save()
                for opt_text in form.cleaned_data["options_text"]:
                    PollOption.objects.create(poll=poll, text=opt_text)
            messages.success(request, f"Poll '{poll.question}' created successfully.")
            return redirect("poll_list")
    else:
        initial = {}
        course_id = request.GET.get("course")
        if course_id:
            initial["course"] = course_id
        form = PollForm(initial=initial)

    return render(
        request,
        "academics/poll_form.html",
        {
            "form": form,
            "title": "Create Class Poll",
        },
    )


@login_required
def poll_detail(request, poll_id):
    poll = get_object_or_404(
        Poll.objects.select_related("course", "created_by"),
        id=poll_id,
    )

    if request.user.role == "student" and poll.course:
        is_enrolled = Enrollment.objects.filter(
            course=poll.course,
            student=request.user,
            course__is_active=True,
        ).exists()
        if not is_enrolled:
            return HttpResponseForbidden("You are not enrolled in the course for this poll.")

    user_vote = PollVote.objects.filter(
        poll=poll,
        student=request.user,
    ).select_related("option").first()

    if request.method == "POST":
        if request.user.role != "student":
            messages.error(request, "Only students can cast votes in class polls.")
            return redirect("poll_detail", poll_id=poll.id)

        if not poll.is_active():
            messages.error(request, "This poll is not currently open for voting.")
            return redirect("poll_detail", poll_id=poll.id)

        if user_vote:
            messages.warning(request, "You have already cast your vote in this poll.")
            return redirect("poll_detail", poll_id=poll.id)

        form = PollVoteForm(request.POST, poll=poll)
        if form.is_valid():
            selected_option = form.cleaned_data["option"]
            try:
                with transaction.atomic():
                    PollVote.objects.create(
                        poll=poll,
                        option=selected_option,
                        student=request.user,
                    )
                messages.success(request, "Your vote has been recorded successfully.")
                return redirect("poll_detail", poll_id=poll.id)
            except IntegrityError:
                messages.warning(request, "You have already cast your vote in this poll.")
                return redirect("poll_detail", poll_id=poll.id)
    else:
        form = PollVoteForm(poll=poll)

    can_view_results = poll.can_user_view_results(request.user)
    results_data = []
    if can_view_results:
        for opt in poll.options.all():
            results_data.append({
                "option": opt,
                "count": opt.vote_count,
                "percentage": opt.vote_percentage(),
                "is_selected": bool(user_vote and user_vote.option_id == opt.id),
            })

    return render(
        request,
        "academics/poll_detail.html",
        {
            "poll": poll,
            "form": form,
            "user_vote": user_vote,
            "can_view_results": can_view_results,
            "results_data": results_data,
            "total_votes": poll.total_votes,
        },
    )


@login_required
@user_passes_test(is_admin)
def close_poll(request, poll_id):
    poll = get_object_or_404(Poll, id=poll_id)
    if request.method == "POST":
        poll.is_closed = True
        poll.save()
        messages.success(request, f"Poll '{poll.question}' has been closed.")
        return redirect("poll_list")

    return render(
        request,
        "academics/poll_confirm_close.html",
        {"poll": poll},
    )

