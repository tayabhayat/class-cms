import io
from datetime import date, timedelta
import openpyxl

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import (
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

User = get_user_model()



def make_excel(headers, rows, filename="attendance.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["PUCIT Attendance Spreadsheet"])  # Row 1: title/metadata
    ws.append(headers)                           # Row 2: headers
    for r in rows:                               # Row 3+: data
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return SimpleUploadedFile(
        filename,
        buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class AttendanceImportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.student1 = User.objects.create_user(
            email="student1@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A001",
            first_name="Ayesha",
            last_name="Saleem",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="student2@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A002",
            first_name="Bilal",
            last_name="Tariq",
            must_change_password=False,
        )
        self.unenrolled_student = User.objects.create_user(
            email="student3@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A003",
            first_name="Usman",
            last_name="Ali",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="HQ-005",
            name="Quran Translation",
            semester="Semester 5",
            is_active=True,
        )
        Enrollment.objects.create(student=self.student1, course=self.course)
        Enrollment.objects.create(student=self.student2, course=self.course)

        self.import_url = reverse("import_attendance")
        self.date1 = date(2026, 9, 12)
        self.date2 = date(2026, 9, 19)

    def test_permission_checks(self):
        # 1. Anonymous user redirected to login
        res_anon = self.client.get(self.import_url)
        self.assertEqual(res_anon.status_code, 302)
        self.assertIn("/login", res_anon.url)

        # 2. Student user redirected to login
        self.client.force_login(self.student1)
        res_student = self.client.get(self.import_url)
        self.assertEqual(res_student.status_code, 302)
        self.assertIn("/login", res_student.url)

        # 3. Admin user allowed access
        self.client.force_login(self.admin)
        res_admin = self.client.get(self.import_url)
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Import Attendance")
        self.assertFalse(res_admin.context["can_confirm"])

    def test_preview_only_does_not_modify_database(self):
        self.client.force_login(self.admin)
        excel_file = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1, self.date2],
            rows=[
                [1, "BITF24A001", "Ayesha Saleem", "P", "A"],
                [2, "BITF24A002", "Bilal Tariq", "P", "P"],
            ],
        )

        response = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file},
        )

        self.assertEqual(response.status_code, 200)
        preview = response.context["preview"]
        self.assertIsNotNone(preview)
        self.assertEqual(len(preview["new"]), 4)
        self.assertEqual(len(preview["changed"]), 0)
        self.assertEqual(len(preview["unchanged"]), 0)
        self.assertEqual(response.context["errors"], [])
        self.assertTrue(response.context["can_confirm"])

        # Crucial check: database was not touched
        self.assertEqual(AttendanceSession.objects.count(), 0)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_successful_confirmation(self):
        self.client.force_login(self.admin)
        excel_file = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1, self.date2],
            rows=[
                [1, "BITF24A001", "Ayesha Saleem", "P", "A"],
                [2, "BITF24A002", "Bilal Tariq", "P", "P"],
            ],
        )

        response = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file, "action": "confirm"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        # Check success message
        messages = list(get_messages(response.wsgi_request))
        self.assertTrue(any("Attendance import completed successfully" in str(m) for m in messages))
        self.assertTrue(any("4 added" in str(m) for m in messages))

        # Check AttendanceSession created with created_by = admin
        sessions = AttendanceSession.objects.filter(course=self.course).order_by("date")
        self.assertEqual(sessions.count(), 2)
        self.assertEqual(sessions[0].date, self.date1)
        self.assertEqual(sessions[0].created_by, self.admin)
        self.assertEqual(sessions[1].date, self.date2)

        # Check Attendance records
        self.assertEqual(Attendance.objects.count(), 4)
        a1 = Attendance.objects.get(session=sessions[0], student=self.student1)
        self.assertEqual(a1.status, Attendance.Status.PRESENT)
        a2 = Attendance.objects.get(session=sessions[1], student=self.student1)
        self.assertEqual(a2.status, Attendance.Status.ABSENT)

    def test_repeated_import_identical_data(self):
        self.client.force_login(self.admin)
        excel_file = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1],
            rows=[
                [1, "BITF24A001", "Ayesha Saleem", "P"],
                [2, "BITF24A002", "Bilal Tariq", "P"],
            ],
        )

        # 1. First import
        self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file, "action": "confirm"},
        )
        self.assertEqual(AttendanceSession.objects.count(), 1)
        self.assertEqual(Attendance.objects.count(), 2)

        # 2. Preview the exact same file again
        excel_file.seek(0)
        res_preview = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file},
        )
        preview = res_preview.context["preview"]
        self.assertEqual(len(preview["new"]), 0)
        self.assertEqual(len(preview["changed"]), 0)
        self.assertEqual(len(preview["unchanged"]), 2)
        # Can confirm should be False because 0 new and 0 changed
        self.assertFalse(res_preview.context["can_confirm"])

        # 3. If confirm is attempted, no duplicates are created
        excel_file.seek(0)
        res_confirm = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file, "action": "confirm"},
            follow=True,
        )
        self.assertEqual(AttendanceSession.objects.count(), 1)
        self.assertEqual(Attendance.objects.count(), 2)
        messages = list(get_messages(res_confirm.wsgi_request))
        self.assertTrue(any("No new or changed attendance records to import" in str(m) for m in messages))

    def test_changed_statuses(self):
        self.client.force_login(self.admin)
        # 1. Initial import: student1 is Present on date1
        excel_file1 = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1],
            rows=[[1, "BITF24A001", "Ayesha Saleem", "P"]],
        )
        self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file1, "action": "confirm"},
        )
        rec = Attendance.objects.get(student=self.student1, session__date=self.date1)
        self.assertEqual(rec.status, Attendance.Status.PRESENT)

        # 2. Upload file where student1 is now Absent on date1
        excel_file2 = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1],
            rows=[[1, "BITF24A001", "Ayesha Saleem", "A"]],
        )
        res_preview = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file2},
        )
        preview = res_preview.context["preview"]
        self.assertEqual(len(preview["new"]), 0)
        self.assertEqual(len(preview["changed"]), 1)
        self.assertEqual(preview["changed"][0]["existing_status"], "Present")
        self.assertEqual(preview["changed"][0]["status"], "absent")
        self.assertTrue(res_preview.context["can_confirm"])

        # 3. Confirm change
        excel_file2.seek(0)
        self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file2, "action": "confirm"},
        )
        rec.refresh_from_db()
        self.assertEqual(rec.status, Attendance.Status.ABSENT)
        self.assertEqual(Attendance.objects.count(), 1)

    def test_blank_cells_do_not_overwrite_existing_attendance(self):
        self.client.force_login(self.admin)
        # Initial import: student1 has Present on date1 and Absent on date2
        excel_file1 = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1, self.date2],
            rows=[[1, "BITF24A001", "Ayesha Saleem", "P", "A"]],
        )
        self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file1, "action": "confirm"},
        )

        # Second import: date1 is blank (None), date2 is "P"
        excel_file2 = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1, self.date2],
            rows=[[1, "BITF24A001", "Ayesha Saleem", None, "P"]],
        )
        res_preview = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file2},
        )
        preview = res_preview.context["preview"]
        self.assertEqual(len(preview["new"]), 0)
        self.assertEqual(len(preview["changed"]), 1)
        self.assertEqual(preview["changed"][0]["date"], self.date2.isoformat())
        # Date1 should NOT be in changed, new, or unchanged
        self.assertEqual(len(preview["unchanged"]), 0)
        self.assertEqual(res_preview.context["skipped_count"], 1)

        # Confirm import
        excel_file2.seek(0)
        self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file2, "action": "confirm"},
        )

        # Check date1 record was preserved
        rec_date1 = Attendance.objects.get(student=self.student1, session__date=self.date1)
        self.assertEqual(rec_date1.status, Attendance.Status.PRESENT)

        # Check date2 record was updated
        rec_date2 = Attendance.objects.get(student=self.student1, session__date=self.date2)
        self.assertEqual(rec_date2.status, Attendance.Status.PRESENT)

    def test_unknown_and_unenrolled_student_ids_rejected(self):
        self.client.force_login(self.admin)
        # Sheet contains:
        # - BITF24A001 (enrolled)
        # - UNKNOWN999 (unknown student ID)
        # - BITF24A003 (valid user, but unenrolled in HQ-005)
        excel_file = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1],
            rows=[
                [1, "BITF24A001", "Ayesha Saleem", "P"],
                [2, "UNKNOWN999", "Unknown Student", "P"],
                [3, "BITF24A003", "Usman Ali", "P"],
            ],
        )

        res_preview = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file},
        )
        errors = res_preview.context["errors"]
        self.assertTrue(any("UNKNOWN999: unknown student ID" in e for e in errors))
        self.assertTrue(any("BITF24A003: student is not enrolled in HQ-005" in e for e in errors))
        self.assertFalse(res_preview.context["can_confirm"])

        # Attempting confirmation must fail and save nothing
        excel_file.seek(0)
        res_confirm = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file, "action": "confirm"},
        )
        self.assertEqual(AttendanceSession.objects.count(), 0)
        self.assertEqual(Attendance.objects.count(), 0)
        messages = list(get_messages(res_confirm.wsgi_request))
        self.assertTrue(any("Attendance import cancelled" in str(m) for m in messages))

    def test_duplicate_student_id_in_spreadsheet_rejected(self):
        self.client.force_login(self.admin)
        excel_file = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1],
            rows=[
                [1, "BITF24A001", "Ayesha Saleem", "P"],
                [2, "BITF24A001", "Ayesha Saleem Duplicate", "A"],
            ],
        )

        res_preview = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file},
        )
        errors = res_preview.context["errors"]
        self.assertTrue(any("duplicate student ID BITF24A001" in e for e in errors))
        self.assertFalse(res_preview.context["can_confirm"])

        # Attempting confirm must not save
        excel_file.seek(0)
        self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file, "action": "confirm"},
        )
        self.assertEqual(Attendance.objects.count(), 0)

    def test_invalid_status_rejected(self):
        self.client.force_login(self.admin)
        excel_file = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1],
            rows=[[1, "BITF24A001", "Ayesha Saleem", "INVALID_STATUS"]],
        )

        res_preview = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file},
        )
        errors = res_preview.context["errors"]
        self.assertTrue(any("unknown status" in e for e in errors))
        self.assertFalse(res_preview.context["can_confirm"])

        excel_file.seek(0)
        self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file, "action": "confirm"},
        )
        self.assertEqual(Attendance.objects.count(), 0)

    def test_malformed_workbook_rejected(self):
        self.client.force_login(self.admin)
        # Missing Student ID header
        excel_file = make_excel(
            headers=[None, "Wrong Header", "Student Name", self.date1],
            rows=[[1, "BITF24A001", "Ayesha Saleem", "P"]],
        )

        res = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file},
        )
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.context["can_confirm"])
        self.assertEqual(Attendance.objects.count(), 0)

    def test_session_fallback_confirmation(self):
        # Verify browser UX where confirm is submitted without re-attaching file
        self.client.force_login(self.admin)
        excel_file = make_excel(
            headers=[None, "Student ID", "Student Name", self.date1],
            rows=[[1, "BITF24A001", "Ayesha Saleem", "P"]],
        )

        # 1. Preview stores file in session
        res_preview = self.client.post(
            self.import_url,
            {"course": self.course.id, "file": excel_file},
        )
        self.assertTrue(res_preview.context["can_confirm"])
        self.assertEqual(Attendance.objects.count(), 0)

        # 2. Confirm without file in request.FILES
        res_confirm = self.client.post(
            self.import_url,
            {"course": self.course.id, "action": "confirm"},
            follow=True,
        )
        self.assertEqual(res_confirm.status_code, 200)
        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(
            Attendance.objects.first().status, Attendance.Status.PRESENT
        )


class ManualAttendanceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_manual@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.student1 = User.objects.create_user(
            email="s1@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A101",
            first_name="Hamza",
            last_name="Ali",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="s2@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A102",
            first_name="Zainab",
            last_name="Bibi",
            must_change_password=False,
        )
        self.student3_other_course = User.objects.create_user(
            email="s3@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A103",
            first_name="Omer",
            last_name="Khan",
            must_change_password=False,
        )

        self.course1 = Course.objects.create(
            code="CS-101",
            name="Intro to Programming",
            semester="Semester 1",
            is_active=True,
        )
        self.course2 = Course.objects.create(
            code="CS-102",
            name="Data Structures",
            semester="Semester 2",
            is_active=True,
        )

        Enrollment.objects.create(student=self.student1, course=self.course1)
        Enrollment.objects.create(student=self.student2, course=self.course1)
        Enrollment.objects.create(student=self.student3_other_course, course=self.course2)

        self.mark_url = reverse("mark_attendance", kwargs={"course_id": self.course1.id})
        self.test_date = date(2026, 10, 15)

    def test_admin_can_mark_attendance(self):
        self.client.force_login(self.admin)
        post_data = {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "present",
            f"status_{self.student2.id}": "absent",
        }

        response = self.client.post(self.mark_url, post_data)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"date={self.test_date.isoformat()}", response.url)

        session = AttendanceSession.objects.get(course=self.course1, date=self.test_date)
        self.assertEqual(session.created_by, self.admin)

        rec1 = Attendance.objects.get(session=session, student=self.student1)
        rec2 = Attendance.objects.get(session=session, student=self.student2)
        self.assertEqual(rec1.status, Attendance.Status.PRESENT)
        self.assertEqual(rec2.status, Attendance.Status.ABSENT)

    def test_students_cannot_mark_attendance(self):
        self.client.force_login(self.student1)
        res_get = self.client.get(self.mark_url)
        self.assertEqual(res_get.status_code, 302)
        self.assertIn("/login", res_get.url)

        res_post = self.client.post(self.mark_url, {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "present",
            f"status_{self.student2.id}": "present",
        })
        self.assertEqual(res_post.status_code, 302)
        self.assertIn("/login", res_post.url)
        self.assertEqual(AttendanceSession.objects.filter(course=self.course1).count(), 0)

    def test_only_enrolled_students_appear_for_selected_course(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.mark_url)
        self.assertEqual(response.status_code, 200)

        students_in_context = list(response.context["students"])
        self.assertIn(self.student1, students_in_context)
        self.assertIn(self.student2, students_in_context)
        self.assertNotIn(self.student3_other_course, students_in_context)

    def test_invalid_student_ids_and_course_mismatches_rejected(self):
        self.client.force_login(self.admin)
        # Attempt to submit status for student3 who is not enrolled in course1
        post_data = {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "present",
            f"status_{self.student2.id}": "present",
            f"status_{self.student3_other_course.id}": "present",
        }

        response = self.client.post(self.mark_url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Student with ID {self.student3_other_course.id} is not enrolled in CS-101")
        self.assertEqual(AttendanceSession.objects.filter(course=self.course1).count(), 0)
        self.assertEqual(Attendance.objects.count(), 0)

        # Attempt to submit an invalid non-integer student key
        post_data_bad_id = {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "present",
            f"status_{self.student2.id}": "present",
            "status_xyz": "present",
        }
        res_bad = self.client.post(self.mark_url, post_data_bad_id)
        self.assertEqual(res_bad.status_code, 200)
        self.assertContains(res_bad, "Invalid student identifier")
        self.assertEqual(AttendanceSession.objects.filter(course=self.course1).count(), 0)

    def test_existing_records_can_be_corrected(self):
        self.client.force_login(self.admin)
        # Create existing session and attendance: student1 is Present, student2 is Absent
        session = AttendanceSession.objects.create(
            course=self.course1,
            date=self.test_date,
            created_by=self.admin,
        )
        Attendance.objects.create(session=session, student=self.student1, status="present")
        Attendance.objects.create(session=session, student=self.student2, status="absent")

        # Load date in UI via GET
        res_get = self.client.get(f"{self.mark_url}?date={self.test_date.isoformat()}")
        self.assertEqual(res_get.status_code, 200)
        students_by_id = {s.id: s for s in res_get.context["students"]}
        self.assertEqual(students_by_id[self.student1.id].current_status, "present")
        self.assertEqual(students_by_id[self.student2.id].current_status, "absent")

        # Correct attendance: student1 -> Absent, student2 -> Present
        post_data = {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "absent",
            f"status_{self.student2.id}": "present",
        }
        res_post = self.client.post(self.mark_url, post_data)
        self.assertEqual(res_post.status_code, 302)

        # Confirm DB was updated in-place without creating extra rows
        self.assertEqual(AttendanceSession.objects.filter(course=self.course1).count(), 1)
        self.assertEqual(Attendance.objects.filter(session=session).count(), 2)

        rec1 = Attendance.objects.get(session=session, student=self.student1)
        rec2 = Attendance.objects.get(session=session, student=self.student2)
        self.assertEqual(rec1.status, Attendance.Status.ABSENT)
        self.assertEqual(rec2.status, Attendance.Status.PRESENT)

    def test_duplicate_attendance_records_prevented(self):
        self.client.force_login(self.admin)
        post_data = {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "present",
            f"status_{self.student2.id}": "present",
        }

        # First post
        self.client.post(self.mark_url, post_data)
        self.assertEqual(AttendanceSession.objects.filter(course=self.course1, date=self.test_date).count(), 1)
        self.assertEqual(Attendance.objects.count(), 2)

        # Repeated post for the same course and date
        self.client.post(self.mark_url, post_data)
        self.assertEqual(AttendanceSession.objects.filter(course=self.course1, date=self.test_date).count(), 1)
        self.assertEqual(Attendance.objects.count(), 2)

    def test_failed_batch_saves_do_not_leave_partial_changes(self):
        self.client.force_login(self.admin)
        # student1 has valid status, student2 has invalid status
        post_data = {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "present",
            f"status_{self.student2.id}": "invalid_status",
        }

        res = self.client.post(self.mark_url, post_data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Select a valid status")

        # Verify nothing was saved to the DB
        self.assertEqual(AttendanceSession.objects.filter(course=self.course1).count(), 0)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_does_not_overwrite_unrelated_courses_or_dates(self):
        self.client.force_login(self.admin)
        other_date = date(2026, 10, 16)
        # Create session for course1 on other_date
        s1 = AttendanceSession.objects.create(course=self.course1, date=other_date, created_by=self.admin)
        Attendance.objects.create(session=s1, student=self.student1, status="present")

        # Create session for course2 on test_date
        s2 = AttendanceSession.objects.create(course=self.course2, date=self.test_date, created_by=self.admin)
        Attendance.objects.create(session=s2, student=self.student3_other_course, status="present")

        # Now mark attendance for course1 on test_date
        post_data = {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "absent",
            f"status_{self.student2.id}": "absent",
        }
        self.client.post(self.mark_url, post_data)

        # Other date for course1 was not touched
        rec_s1 = Attendance.objects.get(session=s1, student=self.student1)
        self.assertEqual(rec_s1.status, Attendance.Status.PRESENT)

        # Other course for test_date was not touched
        rec_s2 = Attendance.objects.get(session=s2, student=self.student3_other_course)
        self.assertEqual(rec_s2.status, Attendance.Status.PRESENT)

    def test_excel_import_and_manual_attendance_coexist_and_update_same_records(self):
        self.client.force_login(self.admin)
        # 1. Import via Excel for course1
        excel_file = make_excel(
            headers=[None, "Student ID", "Student Name", self.test_date],
            rows=[
                [1, self.student1.student_id, "Hamza Ali", "P"],
                [2, self.student2.student_id, "Zainab Bibi", "A"],
            ],
        )
        self.client.post(
            reverse("import_attendance"),
            {"course": self.course1.id, "file": excel_file, "action": "confirm"},
        )
        self.assertEqual(AttendanceSession.objects.filter(course=self.course1, date=self.test_date).count(), 1)
        rec1 = Attendance.objects.get(session__course=self.course1, session__date=self.test_date, student=self.student1)
        self.assertEqual(rec1.status, Attendance.Status.PRESENT)

        # 2. View in manual attendance: preloads Excel-imported status
        res_get = self.client.get(f"{self.mark_url}?date={self.test_date.isoformat()}")
        self.assertEqual(res_get.status_code, 200)
        students_by_id = {s.id: s for s in res_get.context["students"]}
        self.assertEqual(students_by_id[self.student1.id].current_status, "present")
        self.assertEqual(students_by_id[self.student2.id].current_status, "absent")

        # 3. Manually correct student1 from Present to Absent
        post_data = {
            "date": self.test_date.isoformat(),
            f"status_{self.student1.id}": "absent",
            f"status_{self.student2.id}": "absent",
        }
        res_post = self.client.post(self.mark_url, post_data)
        self.assertEqual(res_post.status_code, 302)

        rec1.refresh_from_db()
        self.assertEqual(rec1.status, Attendance.Status.ABSENT)
        self.assertEqual(Attendance.objects.filter(session__course=self.course1).count(), 2)


class StudentAttendanceSummaryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_summary@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.student1 = User.objects.create_user(
            email="summary1@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A201",
            first_name="Amina",
            last_name="Noor",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="summary2@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A202",
            first_name="Bilal",
            last_name="Ahmed",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="CC-311",
            name="Database Systems",
            semester="Semester 5",
            is_active=True,
        )
        self.course_other = Course.objects.create(
            code="CC-312",
            name="Operating Systems",
            semester="Semester 5",
            is_active=True,
        )
        self.inactive_course = Course.objects.create(
            code="CC-100",
            name="Old Course",
            semester="Semester 1",
            is_active=False,
        )

        Enrollment.objects.create(student=self.student1, course=self.course)
        Enrollment.objects.create(student=self.student2, course=self.course)
        Enrollment.objects.create(student=self.student1, course=self.inactive_course)

        self.summary_url = reverse("my_attendance", kwargs={"course_id": self.course.id})

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(self.summary_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_admin_cannot_access_student_summary(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.summary_url)
        self.assertEqual(response.status_code, 404)

    def test_unenrolled_student_gets_404(self):
        self.client.force_login(self.student1)
        url_other = reverse("my_attendance", kwargs={"course_id": self.course_other.id})
        response = self.client.get(url_other)
        self.assertEqual(response.status_code, 404)

    def test_inactive_course_gets_404(self):
        self.client.force_login(self.student1)
        url_inactive = reverse("my_attendance", kwargs={"course_id": self.inactive_course.id})
        response = self.client.get(url_inactive)
        self.assertEqual(response.status_code, 404)

    def test_zero_sessions_handled_safely_without_division_by_zero(self):
        self.client.force_login(self.student1)
        response = self.client.get(self.summary_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_sessions"], 0)
        self.assertEqual(response.context["present_count"], 0)
        self.assertEqual(response.context["absent_count"], 0)
        self.assertEqual(response.context["leave_count"], 0)
        self.assertEqual(response.context["unrecorded_count"], 0)
        self.assertEqual(response.context["percentage"], 0.0)
        self.assertContains(response, "No attendance sessions have been held yet for this course.")

    def test_enrolled_student_can_view_attendance_summary(self):
        s1 = AttendanceSession.objects.create(
            course=self.course, date=date(2026, 10, 1), created_by=self.admin
        )
        s2 = AttendanceSession.objects.create(
            course=self.course, date=date(2026, 10, 8), created_by=self.admin
        )
        Attendance.objects.create(session=s1, student=self.student1, status="present")
        Attendance.objects.create(session=s2, student=self.student1, status="absent")

        self.client.force_login(self.student1)
        response = self.client.get(self.summary_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "academics/my_attendance.html")

        self.assertEqual(response.context["total_sessions"], 2)
        self.assertEqual(response.context["present_count"], 1)
        self.assertEqual(response.context["absent_count"], 1)
        self.assertEqual(response.context["percentage"], 50.0)

        self.assertContains(response, "CC-311")
        self.assertContains(response, "Database Systems")
        self.assertContains(response, "Amina Noor")
        self.assertContains(response, "BITF24A201")
        self.assertContains(response, "Present")
        self.assertContains(response, "Absent")

    def test_percentage_calculation_precision_and_leave_handling(self):
        # 3 present, 1 leave -> total 4 sessions, present 3, leave 1 -> 75.0%
        d1 = AttendanceSession.objects.create(course=self.course, date=date(2026, 9, 1), created_by=self.admin)
        d2 = AttendanceSession.objects.create(course=self.course, date=date(2026, 9, 8), created_by=self.admin)
        d3 = AttendanceSession.objects.create(course=self.course, date=date(2026, 9, 15), created_by=self.admin)
        d4 = AttendanceSession.objects.create(course=self.course, date=date(2026, 9, 22), created_by=self.admin)

        Attendance.objects.create(session=d1, student=self.student1, status="present")
        Attendance.objects.create(session=d2, student=self.student1, status="present")
        Attendance.objects.create(session=d3, student=self.student1, status="present")
        Attendance.objects.create(session=d4, student=self.student1, status="leave")

        self.client.force_login(self.student1)
        res = self.client.get(self.summary_url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["total_sessions"], 4)
        self.assertEqual(res.context["present_count"], 3)
        self.assertEqual(res.context["leave_count"], 1)
        self.assertEqual(res.context["absent_count"], 0)
        self.assertEqual(res.context["percentage"], 75.0)

        # Add 5th session marked absent -> 3 present out of 5 -> 60.0%
        d5 = AttendanceSession.objects.create(course=self.course, date=date(2026, 9, 29), created_by=self.admin)
        Attendance.objects.create(session=d5, student=self.student1, status="absent")

        res2 = self.client.get(self.summary_url)
        self.assertEqual(res2.context["total_sessions"], 5)
        self.assertEqual(res2.context["present_count"], 3)
        self.assertEqual(res2.context["absent_count"], 1)
        self.assertEqual(res2.context["leave_count"], 1)
        self.assertEqual(res2.context["percentage"], 60.0)

    def test_missing_attendance_records_not_counted_as_present(self):
        s1 = AttendanceSession.objects.create(course=self.course, date=date(2026, 10, 1), created_by=self.admin)
        s2 = AttendanceSession.objects.create(course=self.course, date=date(2026, 10, 8), created_by=self.admin)
        # Only record attendance for s1, s2 is unrecorded for student1
        Attendance.objects.create(session=s1, student=self.student1, status="present")

        self.client.force_login(self.student1)
        res = self.client.get(self.summary_url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["total_sessions"], 2)
        self.assertEqual(res.context["present_count"], 1)
        self.assertEqual(res.context["unrecorded_count"], 1)
        self.assertEqual(res.context["percentage"], 50.0)
        self.assertContains(res, "Not Marked")

    def test_student_sees_only_own_records(self):
        s = AttendanceSession.objects.create(course=self.course, date=date(2026, 10, 1), created_by=self.admin)
        Attendance.objects.create(session=s, student=self.student1, status="present")
        Attendance.objects.create(session=s, student=self.student2, status="absent")

        # Student 1 view
        self.client.force_login(self.student1)
        res1 = self.client.get(self.summary_url)
        self.assertEqual(res1.context["present_count"], 1)
        self.assertEqual(res1.context["absent_count"], 0)
        self.assertEqual(res1.context["percentage"], 100.0)

        # Student 2 view
        self.client.force_login(self.student2)
        res2 = self.client.get(self.summary_url)
        self.assertEqual(res2.context["present_count"], 0)
        self.assertEqual(res2.context["absent_count"], 1)
        self.assertEqual(res2.context["percentage"], 0.0)

    def test_navigation_and_permission_isolation(self):
        # 1. Student views dashboard: sees My Attendance button, does not see Mark Attendance
        self.client.force_login(self.student1)
        res_dash = self.client.get(reverse("dashboard"))
        self.assertEqual(res_dash.status_code, 200)
        self.assertContains(res_dash, reverse("my_attendance", kwargs={"course_id": self.course.id}))
        self.assertNotContains(res_dash, reverse("mark_attendance", kwargs={"course_id": self.course.id}))

        # 2. Student views course detail: sees View My Attendance, does not see Mark Attendance or Import Attendance
        res_detail = self.client.get(reverse("course_detail", kwargs={"course_id": self.course.id}))
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, reverse("my_attendance", kwargs={"course_id": self.course.id}))
        self.assertNotContains(res_detail, reverse("mark_attendance", kwargs={"course_id": self.course.id}))
        self.assertNotContains(res_detail, reverse("import_attendance"))

        # 3. Admin views dashboard: sees Mark Attendance, does not see My Attendance
        self.client.force_login(self.admin)
        res_admin_dash = self.client.get(reverse("dashboard"))
        self.assertEqual(res_admin_dash.status_code, 200)
        self.assertContains(res_admin_dash, reverse("mark_attendance", kwargs={"course_id": self.course.id}))
        self.assertNotContains(res_admin_dash, reverse("my_attendance", kwargs={"course_id": self.course.id}))


class CourseManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_courses@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.student = User.objects.create_user(
            email="student_courses@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A301",
            first_name="Hassan",
            last_name="Raza",
            must_change_password=False,
        )
        self.active_course = Course.objects.create(
            code="CS-201",
            name="Data Structures & Algorithms",
            semester="Semester 3",
            is_active=True,
        )
        self.inactive_course = Course.objects.create(
            code="CS-202",
            name="Discrete Mathematics",
            semester="Semester 2",
            is_active=False,
        )
        Enrollment.objects.create(student=self.student, course=self.active_course)
        Enrollment.objects.create(student=self.student, course=self.inactive_course)

        self.manage_url = reverse("manage_courses")
        self.create_url = reverse("create_course")
        self.edit_url = reverse("edit_course", kwargs={"course_id": self.active_course.id})
        self.toggle_url = reverse("toggle_course_status", kwargs={"course_id": self.active_course.id})

    def test_admin_can_view_course_management_list(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.manage_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "academics/manage_courses.html")
        self.assertContains(response, "CS-201")
        self.assertContains(response, "CS-202")
        self.assertContains(response, "Active")
        self.assertContains(response, "Inactive")
        self.assertEqual(response.context["total_courses"], 2)
        self.assertEqual(response.context["active_courses"], 1)
        self.assertEqual(response.context["inactive_courses"], 1)

    def test_admin_can_create_valid_course(self):
        self.client.force_login(self.admin)
        post_data = {
            "code": "se-301",
            "name": "Software Engineering",
            "semester": "Semester 5",
            "is_active": True,
        }
        response = self.client.post(self.create_url, post_data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.manage_url)

        new_course = Course.objects.get(code="SE-301")
        self.assertEqual(new_course.name, "Software Engineering")
        self.assertEqual(new_course.semester, "Semester 5")
        self.assertTrue(new_course.is_active)

    def test_create_course_rejects_duplicate_code(self):
        self.client.force_login(self.admin)
        post_data = {
            "code": "cs-201",  # duplicate case-insensitive
            "name": "Duplicate Course Name",
            "semester": "Semester 1",
            "is_active": True,
        }
        response = self.client.post(self.create_url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"], "code", "A course with code 'CS-201' already exists."
        )
        self.assertEqual(Course.objects.filter(name="Duplicate Course Name").count(), 0)

    def test_create_course_rejects_missing_required_fields(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.create_url, {"code": "", "name": "", "semester": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(Course.objects.count(), 2)

    def test_admin_can_edit_course_without_duplication(self):
        self.client.force_login(self.admin)
        post_data = {
            "code": "CS-201",
            "name": "Advanced Data Structures",
            "semester": "Semester 4",
            "is_active": True,
        }
        response = self.client.post(self.edit_url, post_data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.manage_url)

        self.assertEqual(Course.objects.count(), 2)
        self.active_course.refresh_from_db()
        self.assertEqual(self.active_course.name, "Advanced Data Structures")
        self.assertEqual(self.active_course.semester, "Semester 4")

    def test_edit_course_preserves_enrollments_and_attendance(self):
        # Create attendance session and record
        session = AttendanceSession.objects.create(
            course=self.active_course,
            date=date(2026, 10, 5),
            created_by=self.admin,
        )
        record = Attendance.objects.create(
            session=session,
            student=self.student,
            status=Attendance.Status.PRESENT,
        )

        self.client.force_login(self.admin)
        post_data = {
            "code": "DSA-201",
            "name": "Data Structures Updated",
            "semester": "Semester 3",
            "is_active": True,
        }
        response = self.client.post(self.edit_url, post_data)
        self.assertEqual(response.status_code, 302)

        # Verify course updated in-place and foreign keys preserved
        self.active_course.refresh_from_db()
        self.assertEqual(self.active_course.code, "DSA-201")
        self.assertEqual(Enrollment.objects.filter(course=self.active_course, student=self.student).count(), 1)
        self.assertEqual(AttendanceSession.objects.filter(course=self.active_course).count(), 1)
        self.assertEqual(Attendance.objects.filter(session=session).count(), 1)

    def test_admin_can_deactivate_and_activate_course_with_confirmation(self):
        self.client.force_login(self.admin)

        # 1. GET shows confirmation page with consequences
        res_get = self.client.get(self.toggle_url)
        self.assertEqual(res_get.status_code, 200)
        self.assertTemplateUsed(res_get, "academics/course_confirm_toggle.html")
        self.assertContains(res_get, "Confirm Course Deactivation")

        # 2. POST deactivates course
        res_post = self.client.post(self.toggle_url)
        self.assertEqual(res_post.status_code, 302)
        self.active_course.refresh_from_db()
        self.assertFalse(self.active_course.is_active)

        # 3. GET now shows activate confirmation
        res_get_activate = self.client.get(self.toggle_url)
        self.assertEqual(res_get_activate.status_code, 200)
        self.assertContains(res_get_activate, "Confirm Course Activation")

        # 4. POST activates course
        res_post_activate = self.client.post(self.toggle_url)
        self.assertEqual(res_post_activate.status_code, 302)
        self.active_course.refresh_from_db()
        self.assertTrue(self.active_course.is_active)

    def test_inactive_course_blocks_attendance_creation(self):
        self.client.force_login(self.admin)
        mark_url = reverse("mark_attendance", kwargs={"course_id": self.inactive_course.id})

        # GET displays warning
        res_get = self.client.get(mark_url)
        self.assertEqual(res_get.status_code, 200)
        self.assertContains(res_get, "is currently inactive")

        # POST is blocked and creates no session
        post_data = {
            "date": "2026-10-16",
            f"status_{self.student.id}": "present",
        }
        res_post = self.client.post(mark_url, post_data)
        self.assertEqual(res_post.status_code, 302)
        self.assertEqual(
            AttendanceSession.objects.filter(course=self.inactive_course).count(), 0
        )

    def test_inactive_course_hidden_from_students(self):
        self.client.force_login(self.student)

        # Inactive course is not shown on dashboard
        res_dash = self.client.get(reverse("dashboard"))
        self.assertContains(res_dash, "CS-201")
        self.assertNotContains(res_dash, "CS-202")

        # Inactive course detail returns 404
        res_detail = self.client.get(reverse("course_detail", kwargs={"course_id": self.inactive_course.id}))
        self.assertEqual(res_detail.status_code, 404)

        # Inactive course attendance summary returns 404
        res_summary = self.client.get(reverse("my_attendance", kwargs={"course_id": self.inactive_course.id}))
        self.assertEqual(res_summary.status_code, 404)

    def test_students_cannot_access_course_management(self):
        self.client.force_login(self.student)

        urls = [
            self.manage_url,
            self.create_url,
            self.edit_url,
            self.toggle_url,
        ]
        for url in urls:
            res_get = self.client.get(url)
            self.assertEqual(res_get.status_code, 302)
            self.assertIn("/login", res_get.url)

            res_post = self.client.post(url, {"name": "Hacked Course"})
            self.assertEqual(res_post.status_code, 302)
            self.assertIn("/login", res_post.url)

    def test_navigation_and_ui_permissions(self):
        # Admin navigation
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse("dashboard"))
        self.assertContains(res_admin, reverse("manage_courses"))
        self.assertContains(res_admin, "Manage Courses")

        res_course_detail = self.client.get(reverse("course_detail", kwargs={"course_id": self.active_course.id}))
        self.assertContains(res_course_detail, reverse("edit_course", kwargs={"course_id": self.active_course.id}))
        self.assertContains(res_course_detail, reverse("toggle_course_status", kwargs={"course_id": self.active_course.id}))

        # Student navigation
        self.client.force_login(self.student)
        res_student = self.client.get(reverse("dashboard"))
        self.assertNotContains(res_student, f'href="{reverse("manage_courses")}"')
        self.assertNotContains(res_student, "Manage Courses")

        res_student_detail = self.client.get(reverse("course_detail", kwargs={"course_id": self.active_course.id}))
        self.assertNotContains(res_student_detail, reverse("edit_course", kwargs={"course_id": self.active_course.id}))
        self.assertNotContains(res_student_detail, reverse("toggle_course_status", kwargs={"course_id": self.active_course.id}))


class AnnouncementManagementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_announcements@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
            first_name="Admin",
            last_name="User",
        )
        self.other_admin = User.objects.create_user(
            email="other_admin@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
            first_name="Other",
            last_name="Admin",
        )
        self.student = User.objects.create_user(
            email="student_announcements@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A401",
            first_name="Zayd",
            last_name="Khan",
            must_change_password=False,
        )
        self.announcement = Announcement.objects.create(
            title="Midterm Schedule Fall 2026",
            message="Midterm exams will commence from November 10, 2026.",
            created_by=self.admin,
        )

        self.list_url = reverse("announcement_list")
        self.create_url = reverse("create_announcement")
        self.edit_url = reverse("edit_announcement", kwargs={"announcement_id": self.announcement.id})
        self.delete_url = reverse("delete_announcement", kwargs={"announcement_id": self.announcement.id})

    def test_unauthenticated_user_access(self):
        for url in [self.list_url, self.create_url, self.edit_url, self.delete_url]:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 302)
            self.assertIn("/login", res.url)

    def test_admin_and_student_can_view_announcement_list(self):
        # 1. Student view
        self.client.force_login(self.student)
        res_student = self.client.get(self.list_url)
        self.assertEqual(res_student.status_code, 200)
        self.assertTemplateUsed(res_student, "academics/announcement_list.html")
        self.assertContains(res_student, "Midterm Schedule Fall 2026")
        self.assertContains(res_student, "Midterm exams will commence from November 10, 2026.")
        self.assertContains(res_student, "Admin User")
        # Student must NOT see administrative management buttons
        self.assertNotContains(res_student, self.create_url)
        self.assertNotContains(res_student, self.edit_url)
        self.assertNotContains(res_student, self.delete_url)

        # 2. Admin view
        self.client.force_login(self.admin)
        res_admin = self.client.get(self.list_url)
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Midterm Schedule Fall 2026")
        self.assertContains(res_admin, self.create_url)
        self.assertContains(res_admin, self.edit_url)
        self.assertContains(res_admin, self.delete_url)

    def test_announcement_list_ordering_newest_first(self):
        # Create second announcement
        announcement2 = Announcement.objects.create(
            title="Sports Week Registrations",
            message="Sports week starts next Monday.",
            created_by=self.admin,
        )
        self.client.force_login(self.student)
        res = self.client.get(self.list_url)
        self.assertEqual(res.status_code, 200)
        announcements_in_context = list(res.context["announcements"])
        self.assertEqual(announcements_in_context[0], announcement2)
        self.assertEqual(announcements_in_context[1], self.announcement)

    def test_admin_can_create_announcement_with_author_attribution(self):
        self.client.force_login(self.admin)
        post_data = {
            "title": "Guest Lecture on AI",
            "message": "A guest lecture will take place on Friday in Hall A.",
        }
        res = self.client.post(self.create_url, post_data)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, self.list_url)

        new_announcement = Announcement.objects.get(title="Guest Lecture on AI")
        self.assertEqual(new_announcement.created_by, self.admin)
        self.assertEqual(new_announcement.message, "A guest lecture will take place on Friday in Hall A.")

    def test_create_announcement_validates_required_fields(self):
        self.client.force_login(self.admin)
        # Empty title and empty message
        res = self.client.post(self.create_url, {"title": "", "message": ""})
        self.assertEqual(res.status_code, 200)
        self.assertIn("title", res.context["form"].errors)
        self.assertIn("message", res.context["form"].errors)
        self.assertEqual(Announcement.objects.count(), 1)

    def test_admin_can_edit_announcement_and_author_is_preserved(self):
        # other_admin edits announcement created by admin
        self.client.force_login(self.other_admin)
        post_data = {
            "title": "Midterm Schedule Fall 2026 - Revised",
            "message": "Midterms will now start from November 15, 2026.",
        }
        res = self.client.post(self.edit_url, post_data)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, self.list_url)

        self.announcement.refresh_from_db()
        self.assertEqual(self.announcement.title, "Midterm Schedule Fall 2026 - Revised")
        self.assertEqual(self.announcement.message, "Midterms will now start from November 15, 2026.")
        # Author remains original admin!
        self.assertEqual(self.announcement.created_by, self.admin)

    def test_delete_announcement_requires_confirmation_and_post(self):
        self.client.force_login(self.admin)

        # 1. GET displays confirmation page
        res_get = self.client.get(self.delete_url)
        self.assertEqual(res_get.status_code, 200)
        self.assertTemplateUsed(res_get, "academics/announcement_confirm_delete.html")
        self.assertContains(res_get, "Confirm Announcement Deletion")
        self.assertContains(res_get, "Midterm Schedule Fall 2026")
        self.assertEqual(Announcement.objects.filter(id=self.announcement.id).count(), 1)

        # 2. POST deletes announcement
        res_post = self.client.post(self.delete_url)
        self.assertEqual(res_post.status_code, 302)
        self.assertRedirects(res_post, self.list_url)
        self.assertEqual(Announcement.objects.filter(id=self.announcement.id).count(), 0)

    def test_students_cannot_create_edit_or_delete_announcements(self):
        self.client.force_login(self.student)

        # Attempt create
        res_create_get = self.client.get(self.create_url)
        self.assertEqual(res_create_get.status_code, 302)
        self.assertIn("/login", res_create_get.url)

        res_create_post = self.client.post(self.create_url, {"title": "Unauthorized", "message": "Content"})
        self.assertEqual(res_create_post.status_code, 302)
        self.assertEqual(Announcement.objects.filter(title="Unauthorized").count(), 0)

        # Attempt edit
        res_edit_get = self.client.get(self.edit_url)
        self.assertEqual(res_edit_get.status_code, 302)
        self.assertIn("/login", res_edit_get.url)

        res_edit_post = self.client.post(self.edit_url, {"title": "Hacked", "message": "Content"})
        self.assertEqual(res_edit_post.status_code, 302)
        self.announcement.refresh_from_db()
        self.assertNotEqual(self.announcement.title, "Hacked")

        # Attempt delete
        res_delete_get = self.client.get(self.delete_url)
        self.assertEqual(res_delete_get.status_code, 302)
        self.assertIn("/login", res_delete_get.url)

        res_delete_post = self.client.post(self.delete_url)
        self.assertEqual(res_delete_post.status_code, 302)
        self.assertEqual(Announcement.objects.filter(id=self.announcement.id).count(), 1)

    def test_dashboard_displays_announcements_and_navigation_links(self):
        # 1. Student dashboard
        self.client.force_login(self.student)
        res_student = self.client.get(reverse("dashboard"))
        self.assertEqual(res_student.status_code, 200)
        self.assertContains(res_student, "Midterm Schedule Fall 2026")
        self.assertContains(res_student, self.list_url)
        self.assertNotContains(res_student, self.create_url)
        self.assertNotContains(res_student, self.edit_url)
        self.assertNotContains(res_student, self.delete_url)

        # 2. Admin dashboard
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse("dashboard"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, "Midterm Schedule Fall 2026")
        self.assertContains(res_admin, self.list_url)
        self.assertContains(res_admin, self.create_url)
        self.assertContains(res_admin, self.edit_url)
        self.assertContains(res_admin, self.delete_url)

    def test_sidebar_navigation_announcement_link(self):
        # Admin sidebar has link to announcement_list
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse("dashboard"))
        self.assertContains(res_admin, f'href="{self.list_url}"')

        # Student sidebar has link to announcement_list
        self.client.force_login(self.student)
        res_student = self.client.get(reverse("dashboard"))
        self.assertContains(res_student, f'href="{self.list_url}"')


class TaskAndSubmissionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.student1 = User.objects.create_user(
            email="student1@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A001",
            first_name="Ayesha",
            last_name="Saleem",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="student2@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A002",
            first_name="Bilal",
            last_name="Tariq",
            must_change_password=False,
        )
        self.unenrolled_student = User.objects.create_user(
            email="other@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A003",
            first_name="Usman",
            last_name="Khan",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="CS-301",
            name="Data Structures",
            semester="Fall 2026",
            is_active=True,
        )
        Enrollment.objects.create(student=self.student1, course=self.course)
        Enrollment.objects.create(student=self.student2, course=self.course)

        now = timezone.now()
        self.task = SubmissionTask.objects.create(
            course=self.course,
            title="Assignment 1: Binary Trees",
            instructions="Implement AVL tree balancing in Python.",
            opens_at=now - timedelta(days=2),
            deadline=now + timedelta(days=5),
            deadline_policy=SubmissionTask.DeadlinePolicy.STRICT,
            submission_type=SubmissionTask.SubmissionType.FILE,
            max_file_size_mb=10,
            allowed_extensions=".py,.pdf,.zip",
            created_by=self.admin,
        )

    def test_admin_create_task_success(self):
        self.client.force_login(self.admin)
        now = timezone.now()
        data = {
            "course": self.course.id,
            "title": "Assignment 2: Graphs",
            "instructions": "Graph traversal algorithms.",
            "opens_at": (now).strftime("%Y-%m-%dT%H:%M"),
            "deadline": (now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M"),
            "deadline_policy": "strict",
            "submission_type": "file",
            "max_file_size_mb": 10,
            "allowed_extensions": ".py,.zip",
        }
        res = self.client.post(reverse("create_task"), data)
        self.assertEqual(res.status_code, 302)
        self.assertTrue(SubmissionTask.objects.filter(title="Assignment 2: Graphs").exists())
        created = SubmissionTask.objects.get(title="Assignment 2: Graphs")
        self.assertEqual(created.created_by, self.admin)

    def test_admin_create_task_validation_deadline_before_open(self):
        self.client.force_login(self.admin)
        now = timezone.now()
        data = {
            "course": self.course.id,
            "title": "Invalid Task",
            "instructions": "Invalid dates.",
            "opens_at": (now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
            "deadline": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
            "deadline_policy": "strict",
            "submission_type": "file",
            "max_file_size_mb": 10,
            "allowed_extensions": ".py",
        }
        res = self.client.post(reverse("create_task"), data)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(SubmissionTask.objects.filter(title="Invalid Task").exists())
        self.assertContains(res, "The deadline must be strictly after the opening time.")

    def test_admin_edit_task(self):
        self.client.force_login(self.admin)
        now = timezone.now()
        data = {
            "course": self.course.id,
            "title": "Assignment 1: Updated Title",
            "instructions": "Updated instructions.",
            "opens_at": (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "deadline": (now + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
            "deadline_policy": "allow_late",
            "submission_type": "both",
            "max_file_size_mb": 15,
            "allowed_extensions": ".py,.pdf,.zip,.txt",
        }
        res = self.client.post(reverse("edit_task", kwargs={"task_id": self.task.id}), data)
        self.assertEqual(res.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "Assignment 1: Updated Title")
        self.assertEqual(self.task.deadline_policy, "allow_late")
        self.assertEqual(self.task.submission_type, "both")

    def test_student_cannot_create_or_edit_task(self):
        self.client.force_login(self.student1)
        res_create = self.client.get(reverse("create_task"))
        self.assertEqual(res_create.status_code, 302)  # redirected by user_passes_test

        res_edit = self.client.get(reverse("edit_task", kwargs={"task_id": self.task.id}))
        self.assertEqual(res_edit.status_code, 302)

    def test_task_list_views(self):
        # Admin task list
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse("task_list"))
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, self.task.title)
        self.assertContains(res_admin, "Submissions")

        # Student task list
        self.client.force_login(self.student1)
        res_student = self.client.get(reverse("task_list"))
        self.assertEqual(res_student.status_code, 200)
        self.assertContains(res_student, self.task.title)
        self.assertContains(res_student, "Pending")

    def test_student_submit_file_on_time(self):
        self.client.force_login(self.student1)
        test_file = SimpleUploadedFile("assignment1.py", b"print('AVL Tree implementation')", content_type="text/x-python")
        res = self.client.post(
            reverse("task_detail", kwargs={"task_id": self.task.id}),
            {"file": test_file},
        )
        self.assertEqual(res.status_code, 302)
        submission = Submission.objects.get(task=self.task, student=self.student1)
        self.assertEqual(submission.original_filename, "assignment1.py")
        self.assertFalse(submission.is_late)
        self.assertTrue(submission.file)

    def test_student_resubmission_updates_record(self):
        self.client.force_login(self.student1)
        # 1st submission
        file1 = SimpleUploadedFile("first_draft.py", b"print('draft')", content_type="text/x-python")
        self.client.post(reverse("task_detail", kwargs={"task_id": self.task.id}), {"file": file1})
        self.assertEqual(Submission.objects.filter(task=self.task, student=self.student1).count(), 1)
        sub1 = Submission.objects.get(task=self.task, student=self.student1)
        self.assertEqual(sub1.original_filename, "first_draft.py")

        # 2nd submission (resubmit / update)
        file2 = SimpleUploadedFile("final_version.py", b"print('final')", content_type="text/x-python")
        self.client.post(reverse("task_detail", kwargs={"task_id": self.task.id}), {"file": file2})
        # Check no duplicates created
        self.assertEqual(Submission.objects.filter(task=self.task, student=self.student1).count(), 1)
        sub1.refresh_from_db()
        self.assertEqual(sub1.original_filename, "final_version.py")

    def test_student_strict_deadline_blocks_late_submission(self):
        now = timezone.now()
        strict_past_task = SubmissionTask.objects.create(
            course=self.course,
            title="Expired Strict Task",
            opens_at=now - timedelta(days=5),
            deadline=now - timedelta(days=1),
            deadline_policy=SubmissionTask.DeadlinePolicy.STRICT,
            submission_type=SubmissionTask.SubmissionType.FILE,
            created_by=self.admin,
        )
        self.client.force_login(self.student1)
        test_file = SimpleUploadedFile("late.py", b"print('late')", content_type="text/x-python")
        res = self.client.post(
            reverse("task_detail", kwargs={"task_id": strict_past_task.id}),
            {"file": test_file},
        )
        self.assertEqual(res.status_code, 302)
        # No submission record created
        self.assertFalse(Submission.objects.filter(task=strict_past_task, student=self.student1).exists())

    def test_student_allow_late_marks_submission_as_late(self):
        now = timezone.now()
        allow_late_task = SubmissionTask.objects.create(
            course=self.course,
            title="Expired Allow Late Task",
            opens_at=now - timedelta(days=5),
            deadline=now - timedelta(days=1),
            deadline_policy=SubmissionTask.DeadlinePolicy.ALLOW_LATE,
            submission_type=SubmissionTask.SubmissionType.FILE,
            created_by=self.admin,
        )
        self.client.force_login(self.student1)
        test_file = SimpleUploadedFile("late.py", b"print('late')", content_type="text/x-python")
        res = self.client.post(
            reverse("task_detail", kwargs={"task_id": allow_late_task.id}),
            {"file": test_file},
        )
        self.assertEqual(res.status_code, 302)
        sub = Submission.objects.get(task=allow_late_task, student=self.student1)
        self.assertTrue(sub.is_late)

    def test_file_extension_validation(self):
        self.client.force_login(self.student1)
        # .exe is not in allowed extensions (.py,.pdf,.zip)
        bad_file = SimpleUploadedFile("malicious.exe", b"binary content", content_type="application/octet-stream")
        res = self.client.post(
            reverse("task_detail", kwargs={"task_id": self.task.id}),
            {"file": bad_file},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "File extension &#x27;.exe&#x27; is not allowed")
        self.assertFalse(Submission.objects.filter(task=self.task, student=self.student1).exists())

    def test_file_size_validation(self):
        self.client.force_login(self.student1)
        # Task max is 10 MB. Create a dummy file exceeding 10 MB (11 MB)
        oversized_file = SimpleUploadedFile("huge.py", b"x" * (11 * 1024 * 1024), content_type="text/x-python")
        res = self.client.post(
            reverse("task_detail", kwargs={"task_id": self.task.id}),
            {"file": oversized_file},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "File exceeds maximum allowed size")
        self.assertFalse(Submission.objects.filter(task=self.task, student=self.student1).exists())

    def test_text_and_both_submission_type(self):
        now = timezone.now()
        text_task = SubmissionTask.objects.create(
            course=self.course,
            title="Text Quiz Task",
            opens_at=now - timedelta(days=1),
            deadline=now + timedelta(days=3),
            deadline_policy=SubmissionTask.DeadlinePolicy.STRICT,
            submission_type=SubmissionTask.SubmissionType.TEXT,
            created_by=self.admin,
        )
        self.client.force_login(self.student1)
        res = self.client.post(
            reverse("task_detail", kwargs={"task_id": text_task.id}),
            {"text_answer": "Time complexity of AVL tree lookup is O(log n)."},
        )
        self.assertEqual(res.status_code, 302)
        sub = Submission.objects.get(task=text_task, student=self.student1)
        self.assertEqual(sub.text_answer, "Time complexity of AVL tree lookup is O(log n).")

    def test_unenrolled_student_cannot_access_task(self):
        self.client.force_login(self.unenrolled_student)
        res = self.client.get(reverse("task_detail", kwargs={"task_id": self.task.id}))
        self.assertEqual(res.status_code, 403)

    def test_submission_roster_report(self):
        # student1 submits on time
        Submission.objects.create(
            task=self.task,
            student=self.student1,
            original_filename="a1.py",
            is_late=False,
        )
        self.client.force_login(self.admin)
        res = self.client.get(reverse("task_submission_report", kwargs={"task_id": self.task.id}))
        self.assertEqual(res.status_code, 200)
        # student1 is submitted
        self.assertContains(res, self.student1.student_id)
        self.assertContains(res, "Submitted")
        # student2 is missing
        self.assertContains(res, self.student2.student_id)
        self.assertContains(res, "Missing")

    def test_download_submission_permissions(self):
        file = SimpleUploadedFile("student1_work.py", b"code", content_type="text/x-python")
        sub = Submission.objects.create(
            task=self.task,
            student=self.student1,
            file=file,
            original_filename="student1_work.py",
        )

        # 1. Admin can download
        self.client.force_login(self.admin)
        res_admin = self.client.get(reverse("download_submission", kwargs={"submission_id": sub.id}))
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin["Content-Disposition"], 'attachment; filename="student1_work.py"')

        # 2. Owner student can download
        self.client.force_login(self.student1)
        res_owner = self.client.get(reverse("download_submission", kwargs={"submission_id": sub.id}))
        self.assertEqual(res_owner.status_code, 200)

        # 3. Other student gets 403 Forbidden
        self.client.force_login(self.student2)
        res_other = self.client.get(reverse("download_submission", kwargs={"submission_id": sub.id}))
        self.assertEqual(res_other.status_code, 403)

    def test_download_task_zip(self):
        file1 = SimpleUploadedFile("sol1.py", b"def f(): pass", content_type="text/x-python")
        Submission.objects.create(
            task=self.task,
            student=self.student1,
            file=file1,
            original_filename="sol1.py",
            text_answer="Solution comments here.",
        )
        self.client.force_login(self.admin)
        res = self.client.get(reverse("download_task_zip", kwargs={"task_id": self.task.id}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/zip")
        self.assertTrue(len(res.content) > 0)


class PollTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.student1 = User.objects.create_user(
            email="student1@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A001",
            first_name="Ayesha",
            last_name="Saleem",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="student2@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A002",
            first_name="Bilal",
            last_name="Tariq",
            must_change_password=False,
        )
        self.unenrolled_student = User.objects.create_user(
            email="other@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A003",
            first_name="Usman",
            last_name="Khan",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="CS-301",
            name="Data Structures",
            semester="Fall 2026",
            is_active=True,
        )
        Enrollment.objects.create(student=self.student1, course=self.course)
        Enrollment.objects.create(student=self.student2, course=self.course)

        now = timezone.now()
        self.poll = Poll.objects.create(
            course=self.course,
            question="When should we hold the review session?",
            description="Please choose your preferred slot.",
            opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=3),
            results_visibility=Poll.ResultsVisibility.ALWAYS,
            created_by=self.admin,
        )
        self.opt1 = PollOption.objects.create(poll=self.poll, text="Monday 10:00 AM")
        self.opt2 = PollOption.objects.create(poll=self.poll, text="Wednesday 2:00 PM")
        self.opt3 = PollOption.objects.create(poll=self.poll, text="Friday 4:00 PM")

    def test_admin_create_poll_success(self):
        self.client.force_login(self.admin)
        now = timezone.now()
        data = {
            "course": self.course.id,
            "question": "Preferred Exam Format?",
            "description": "MCQs vs Coding Problems.",
            "opens_at": now.strftime("%Y-%m-%dT%H:%M"),
            "closes_at": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "results_visibility": "always",
            "options_text": "MCQs Only\nCoding Problems Only\nMixed Format",
        }
        res = self.client.post(reverse("create_poll"), data)
        self.assertEqual(res.status_code, 302)
        created_poll = Poll.objects.get(question="Preferred Exam Format?")
        self.assertEqual(created_poll.options.count(), 3)
        self.assertTrue(created_poll.options.filter(text="Mixed Format").exists())

    def test_poll_validation_min_options(self):
        self.client.force_login(self.admin)
        now = timezone.now()
        data = {
            "course": self.course.id,
            "question": "Single Option Poll?",
            "opens_at": now.strftime("%Y-%m-%dT%H:%M"),
            "closes_at": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "results_visibility": "always",
            "options_text": "Only One Option",
        }
        res = self.client.post(reverse("create_poll"), data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "A poll must have at least 2 distinct options.")
        self.assertFalse(Poll.objects.filter(question="Single Option Poll?").exists())

    def test_student_vote_in_open_poll(self):
        self.client.force_login(self.student1)
        res = self.client.post(
            reverse("poll_detail", kwargs={"poll_id": self.poll.id}),
            {"option": self.opt2.id},
        )
        self.assertEqual(res.status_code, 302)
        self.assertTrue(PollVote.objects.filter(poll=self.poll, student=self.student1, option=self.opt2).exists())
        self.assertEqual(self.poll.total_votes, 1)

    def test_student_cannot_vote_twice(self):
        self.client.force_login(self.student1)
        # First vote
        self.client.post(reverse("poll_detail", kwargs={"poll_id": self.poll.id}), {"option": self.opt1.id})
        self.assertEqual(PollVote.objects.filter(poll=self.poll, student=self.student1).count(), 1)

        # Second vote attempt
        res = self.client.post(reverse("poll_detail", kwargs={"poll_id": self.poll.id}), {"option": self.opt2.id})
        self.assertEqual(res.status_code, 302)
        # Still only 1 vote exists, option unchanged
        self.assertEqual(PollVote.objects.filter(poll=self.poll, student=self.student1).count(), 1)
        self.assertEqual(PollVote.objects.get(poll=self.poll, student=self.student1).option, self.opt1)

    def test_student_cannot_vote_in_closed_poll(self):
        self.poll.is_closed = True
        self.poll.save()

        self.client.force_login(self.student1)
        res = self.client.post(
            reverse("poll_detail", kwargs={"poll_id": self.poll.id}),
            {"option": self.opt1.id},
        )
        self.assertEqual(res.status_code, 302)
        self.assertFalse(PollVote.objects.filter(poll=self.poll, student=self.student1).exists())

    def test_unenrolled_student_cannot_vote_in_course_poll(self):
        self.client.force_login(self.unenrolled_student)
        res = self.client.get(reverse("poll_detail", kwargs={"poll_id": self.poll.id}))
        self.assertEqual(res.status_code, 403)

    def test_general_poll_allows_any_student_to_vote(self):
        now = timezone.now()
        general_poll = Poll.objects.create(
            course=None,  # General class poll
            question="Campus Event Preference?",
            opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=2),
            results_visibility=Poll.ResultsVisibility.ALWAYS,
            created_by=self.admin,
        )
        g_opt1 = PollOption.objects.create(poll=general_poll, text="Hackathon")
        g_opt2 = PollOption.objects.create(poll=general_poll, text="Gaming Tournament")

        self.client.force_login(self.unenrolled_student)
        res = self.client.post(
            reverse("poll_detail", kwargs={"poll_id": general_poll.id}),
            {"option": g_opt1.id},
        )
        self.assertEqual(res.status_code, 302)
        self.assertTrue(PollVote.objects.filter(poll=general_poll, student=self.unenrolled_student).exists())

    def test_results_visibility_always_vs_after_close(self):
        now = timezone.now()
        secret_poll = Poll.objects.create(
            course=self.course,
            question="Secret Ballot Poll",
            opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=2),
            results_visibility=Poll.ResultsVisibility.AFTER_CLOSE,
            created_by=self.admin,
        )
        s_opt1 = PollOption.objects.create(poll=secret_poll, text="Candidate A")
        s_opt2 = PollOption.objects.create(poll=secret_poll, text="Candidate B")

        # 1. Student votes while open
        self.client.force_login(self.student1)
        self.client.post(reverse("poll_detail", kwargs={"poll_id": secret_poll.id}), {"option": s_opt1.id})
        res_voted = self.client.get(reverse("poll_detail", kwargs={"poll_id": secret_poll.id}))
        self.assertContains(res_voted, "Results are Hidden")
        self.assertNotContains(res_voted, "Candidate A (100")

        # 2. Poll is closed
        secret_poll.is_closed = True
        secret_poll.save()
        res_closed = self.client.get(reverse("poll_detail", kwargs={"poll_id": secret_poll.id}))
        self.assertContains(res_closed, "Poll Results")
        self.assertContains(res_closed, "Candidate A")

    def test_admin_close_poll_manually(self):
        self.client.force_login(self.admin)
        res = self.client.post(reverse("close_poll", kwargs={"poll_id": self.poll.id}))
        self.assertEqual(res.status_code, 302)
        self.poll.refresh_from_db()
        self.assertTrue(self.poll.is_closed)

    def test_poll_percentage_calculation(self):
        PollVote.objects.create(poll=self.poll, option=self.opt1, student=self.student1)
        PollVote.objects.create(poll=self.poll, option=self.opt2, student=self.student2)
        self.assertEqual(self.poll.total_votes, 2)
        self.assertEqual(self.opt1.vote_percentage(), 50.0)
        self.assertEqual(self.opt2.vote_percentage(), 50.0)
        self.assertEqual(self.opt3.vote_percentage(), 0)


class SecurityAndHardeningTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="secadmin@pucit.edu.pk",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )
        self.student = User.objects.create_user(
            email="secstudent@pucit.edu.pk",
            password="StudentPassword123!",
            role=User.Role.STUDENT,
            student_id="BITF24A999",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="SEC-101",
            name="Computer Security",
            semester="Fall 2026",
            is_active=True,
        )
        Enrollment.objects.create(student=self.student, course=self.course)

        now = timezone.now()
        self.open_task = SubmissionTask.objects.create(
            course=self.course,
            title="Security Lab 1",
            instructions="Submit lab report.",
            opens_at=now - timedelta(days=1),
            deadline=now + timedelta(days=5),
            deadline_policy=SubmissionTask.DeadlinePolicy.STRICT,
            submission_type=SubmissionTask.SubmissionType.FILE,
            max_file_size_mb=10,
            allowed_extensions="",  # Empty: allows any extension EXCEPT dangerous ones
            created_by=self.admin,
        )

    def test_dangerous_extension_blocked_even_if_allowed_extensions_empty(self):
        self.client.force_login(self.student)
        dangerous_files = ["malware.exe", "payload.html", "exploit.sh", "vector.svg", "script.js"]

        for fname in dangerous_files:
            file_upload = SimpleUploadedFile(fname, b"content", content_type="application/octet-stream")
            res = self.client.post(
                reverse("task_detail", kwargs={"task_id": self.open_task.id}),
                {"file": file_upload},
            )
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "prohibited for security reasons")
            self.assertFalse(Submission.objects.filter(task=self.open_task, student=self.student).exists())

    def test_submission_filename_sanitization_removes_traversal_and_crlf(self):
        self.client.force_login(self.student)
        traversal_name = r"C:\Windows\System32\..\..\malicious.pdf"
        file_upload = SimpleUploadedFile(traversal_name, b"%PDF-1.4 test", content_type="application/pdf")

        res = self.client.post(
            reverse("task_detail", kwargs={"task_id": self.open_task.id}),
            {"file": file_upload},
        )
        self.assertEqual(res.status_code, 302)

        sub = Submission.objects.get(task=self.open_task, student=self.student)
        self.assertNotIn("\\", sub.original_filename)
        self.assertNotIn("..", sub.original_filename)
        self.assertEqual(sub.original_filename, "malicious.pdf")

    def test_download_submission_sanitizes_content_disposition_filename(self):
        self.client.force_login(self.student)
        sub = Submission.objects.create(
            task=self.open_task,
            student=self.student,
            original_filename='evil\r\nHeader: Injection.pdf"',
            file=SimpleUploadedFile("clean.pdf", b"%PDF content"),
        )
        res = self.client.get(reverse("download_submission", kwargs={"submission_id": sub.id}))
        self.assertEqual(res.status_code, 200)
        # Content disposition should not contain raw CRLF or unescaped quotes
        cd = res.headers.get("Content-Disposition", "")
        self.assertNotIn("\r", cd)
        self.assertNotIn("\n", cd)
        self.assertIn("evilHeader Injection.pdf", cd)

    def test_download_task_zip_streams_safe_archive_names(self):
        Submission.objects.create(
            task=self.open_task,
            student=self.student,
            original_filename="..\\..\\secret.pdf",
            file=SimpleUploadedFile("actual.pdf", b"pdf data"),
        )
        self.client.force_login(self.admin)
        res = self.client.get(reverse("download_task_zip", kwargs={"task_id": self.open_task.id}))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Content-Type"), "application/zip")

    def test_poll_voting_option_tampering_rejected(self):
        now = timezone.now()
        poll1 = Poll.objects.create(
            course=self.course,
            question="Poll 1",
            opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=2),
            created_by=self.admin,
        )
        opt_p1 = PollOption.objects.create(poll=poll1, text="Option P1")

        poll2 = Poll.objects.create(
            course=self.course,
            question="Poll 2",
            opens_at=now - timedelta(days=1),
            closes_at=now + timedelta(days=2),
            created_by=self.admin,
        )
        opt_p2 = PollOption.objects.create(poll=poll2, text="Option P2")

        self.client.force_login(self.student)
        # Attempt to vote in Poll 1 submitting option from Poll 2
        res = self.client.post(
            reverse("poll_detail", kwargs={"poll_id": poll1.id}),
            {"option": opt_p2.id},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Select a valid choice")
        self.assertFalse(PollVote.objects.filter(poll=poll1).exists())


class AdminCourseAttendanceViewerTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="cr.admin@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
            first_name="Hassan",
            last_name="Raza",
        )
        self.student1 = User.objects.create_user(
            email="ayesha.saleem@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A001",
            first_name="Ayesha",
            last_name="Saleem",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="bilal.tariq@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A002",
            first_name="Bilal",
            last_name="Tariq",
            must_change_password=False,
        )
        self.student3_no_records = User.objects.create_user(
            email="usman.ali@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A003",
            first_name="Usman",
            last_name="Ali",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="CS-301",
            name="Data Structures & Algorithms",
            semester="Semester 5",
            is_active=True,
        )
        # Enroll all 3 students
        Enrollment.objects.create(student=self.student1, course=self.course)
        Enrollment.objects.create(student=self.student2, course=self.course)
        Enrollment.objects.create(student=self.student3_no_records, course=self.course)

        self.view_url = reverse("view_attendance", kwargs={"course_id": self.course.id})

    def test_anonymous_user_redirected_to_login(self):
        res = self.client.get(self.view_url)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

    def test_student_cannot_access_full_register(self):
        self.client.force_login(self.student1)
        res = self.client.get(self.view_url)
        # Should be redirected away by user_passes_test(is_admin)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

    def test_admin_can_view_course_register(self):
        s1 = AttendanceSession.objects.create(
            course=self.course, date=date(2026, 10, 1), created_by=self.admin
        )
        s2 = AttendanceSession.objects.create(
            course=self.course, date=date(2026, 10, 8), created_by=self.admin
        )
        Attendance.objects.create(session=s1, student=self.student1, status="present")
        Attendance.objects.create(session=s2, student=self.student1, status="absent")

        self.client.force_login(self.admin)
        res = self.client.get(self.view_url)
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "academics/course_attendance.html")
        self.assertContains(res, "CS-301")
        self.assertContains(res, "Data Structures &amp; Algorithms")
        self.assertContains(res, "BITF24A001")
        self.assertContains(res, "Ayesha Saleem")
        self.assertContains(res, "01 Oct")
        self.assertContains(res, "08 Oct")

    def test_enrolled_student_with_no_attendance_records_appears(self):
        s1 = AttendanceSession.objects.create(
            course=self.course, date=date(2026, 10, 1), created_by=self.admin
        )
        Attendance.objects.create(session=s1, student=self.student1, status="present")

        self.client.force_login(self.admin)
        res = self.client.get(self.view_url)
        self.assertEqual(res.status_code, 200)

        student_rows = res.context["student_rows"]
        student_ids_in_context = [r["student"].id for r in student_rows]
        self.assertIn(self.student3_no_records.id, student_ids_in_context)

        row3 = next(r for r in student_rows if r["student"].id == self.student3_no_records.id)
        self.assertEqual(row3["student_id"], "BITF24A003")
        self.assertEqual(row3["name"], "Usman Ali")
        self.assertEqual(row3["present_count"], 0)
        self.assertEqual(row3["absent_count"], 0)
        self.assertEqual(row3["recorded_count"], 0)
        self.assertFalse(row3["has_records"])
        self.assertEqual(row3["percentage"], 0.0)

        self.assertContains(res, "No Records")
        self.assertContains(res, "BITF24A003")

    def test_present_absent_totals_and_percentages_correct(self):
        d1, d2, d3, d4 = date(2026, 10, 1), date(2026, 10, 8), date(2026, 10, 15), date(2026, 10, 22)
        s1 = AttendanceSession.objects.create(course=self.course, date=d1, created_by=self.admin)
        s2 = AttendanceSession.objects.create(course=self.course, date=d2, created_by=self.admin)
        s3 = AttendanceSession.objects.create(course=self.course, date=d3, created_by=self.admin)
        s4 = AttendanceSession.objects.create(course=self.course, date=d4, created_by=self.admin)

        # Student 1: 3 Present, 1 Absent => 3/4 = 75.0%
        Attendance.objects.create(session=s1, student=self.student1, status="present")
        Attendance.objects.create(session=s2, student=self.student1, status="present")
        Attendance.objects.create(session=s3, student=self.student1, status="present")
        Attendance.objects.create(session=s4, student=self.student1, status="absent")

        # Student 2: 1 Present, 2 Absent, 1 unrecorded => 1/4 = 25.0%
        Attendance.objects.create(session=s1, student=self.student2, status="present")
        Attendance.objects.create(session=s2, student=self.student2, status="absent")
        Attendance.objects.create(session=s3, student=self.student2, status="absent")

        self.client.force_login(self.admin)
        res = self.client.get(self.view_url)
        self.assertEqual(res.status_code, 200)

        student_rows = res.context["student_rows"]
        row1 = next(r for r in student_rows if r["student"].id == self.student1.id)
        self.assertEqual(row1["present_count"], 3)
        self.assertEqual(row1["absent_count"], 1)
        self.assertEqual(row1["recorded_count"], 4)
        self.assertEqual(row1["percentage"], 75.0)

        row2 = next(r for r in student_rows if r["student"].id == self.student2.id)
        self.assertEqual(row2["present_count"], 1)
        self.assertEqual(row2["absent_count"], 2)
        self.assertEqual(row2["recorded_count"], 3)
        self.assertEqual(row2["percentage"], 25.0)

        # Class average: (3 + 1) / (4 sessions * 3 students) = 4 / 12 = 33.3%
        self.assertEqual(res.context["class_average_percentage"], 33.3)

    def test_empty_course_and_course_without_sessions_render_without_errors(self):
        # Case 1: Course has enrolled students but zero sessions
        self.client.force_login(self.admin)
        res_no_sessions = self.client.get(self.view_url)
        self.assertEqual(res_no_sessions.status_code, 200)
        self.assertEqual(res_no_sessions.context["total_sessions"], 0)
        self.assertEqual(res_no_sessions.context["class_average_percentage"], 0.0)
        self.assertContains(res_no_sessions, "No attendance sessions have been held yet for this course.")
        self.assertContains(res_no_sessions, "BITF24A001")

        # Case 2: Completely empty course (0 students, 0 sessions)
        empty_course = Course.objects.create(
            code="EMPTY-101", name="Empty Course", semester="Semester 1"
        )
        empty_url = reverse("view_attendance", kwargs={"course_id": empty_course.id})
        res_empty = self.client.get(empty_url)
        self.assertEqual(res_empty.status_code, 200)
        self.assertEqual(res_empty.context["total_students"], 0)
        self.assertEqual(res_empty.context["total_sessions"], 0)
        self.assertEqual(res_empty.context["class_average_percentage"], 0.0)
        self.assertContains(res_empty, "No students are enrolled in this course.")

    def test_course_detail_links_to_view_attendance_for_admin(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("course_detail", kwargs={"course_id": self.course.id}))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.view_url)


class AdminAttendanceEditingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_attendance@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
        )
        self.student1 = User.objects.create_user(
            email="stud1@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A010",
            first_name="Amina",
            last_name="Khan",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="stud2@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A011",
            first_name="Hamza",
            last_name="Raza",
            must_change_password=False,
        )
        self.unenrolled_student = User.objects.create_user(
            email="unenrolled@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A099",
            first_name="Other",
            last_name="Student",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="CS-201",
            name="Data Structures",
            semester="Semester 3",
            is_active=True,
        )
        self.other_course = Course.objects.create(
            code="CS-202",
            name="Algorithms",
            semester="Semester 4",
            is_active=True,
        )
        Enrollment.objects.create(student=self.student1, course=self.course)
        Enrollment.objects.create(student=self.student2, course=self.course)

        self.date1 = date(2026, 10, 1)
        self.date2 = date(2026, 10, 8)

        self.session1 = AttendanceSession.objects.create(
            course=self.course,
            date=self.date1,
            created_by=self.admin,
        )
        self.session2 = AttendanceSession.objects.create(
            course=self.course,
            date=self.date2,
            created_by=self.admin,
        )

        # Initial attendance: student1 present, student2 absent in session1
        self.att1_s1 = Attendance.objects.create(
            session=self.session1,
            student=self.student1,
            status=Attendance.Status.PRESENT,
        )
        self.att1_s2 = Attendance.objects.create(
            session=self.session1,
            student=self.student2,
            status=Attendance.Status.ABSENT,
        )

        # Initial attendance in session2: both present
        self.att2_s1 = Attendance.objects.create(
            session=self.session2,
            student=self.student1,
            status=Attendance.Status.PRESENT,
        )
        self.att2_s2 = Attendance.objects.create(
            session=self.session2,
            student=self.student2,
            status=Attendance.Status.PRESENT,
        )

        self.edit_picker_url = reverse(
            "edit_attendance_picker", kwargs={"course_id": self.course.id}
        )
        self.edit_session1_url = reverse(
            "edit_attendance_session",
            kwargs={"course_id": self.course.id, "session_id": self.session1.id},
        )

    def test_permission_anonymous_user_redirected(self):
        res_get = self.client.get(self.edit_session1_url)
        self.assertEqual(res_get.status_code, 302)
        self.assertIn("/login", res_get.url)

        res_post = self.client.post(self.edit_session1_url, {f"status_{self.student1.id}": "absent"})
        self.assertEqual(res_post.status_code, 302)
        self.assertIn("/login", res_post.url)

    def test_permission_student_user_denied(self):
        self.client.force_login(self.student1)
        res_get = self.client.get(self.edit_session1_url)
        self.assertEqual(res_get.status_code, 302)
        self.assertIn("/login", res_get.url)

        res_post = self.client.post(self.edit_session1_url, {f"status_{self.student1.id}": "absent"})
        self.assertEqual(res_post.status_code, 302)
        self.att1_s1.refresh_from_db()
        self.assertEqual(self.att1_s1.status, Attendance.Status.PRESENT)

    def test_admin_can_view_edit_page(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.edit_session1_url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["selected_session"], self.session1)
        self.assertEqual(len(res.context["students"]), 2)
        self.assertContains(res, "BITF24A010")
        self.assertContains(res, "BITF24A011")

    def test_edit_single_record_present_to_absent(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.edit_session1_url,
            {
                f"status_{self.student1.id}": "absent",
                f"status_{self.student2.id}": "absent",
            },
        )
        self.assertRedirects(res, self.edit_session1_url)
        self.att1_s1.refresh_from_db()
        self.assertEqual(self.att1_s1.status, Attendance.Status.ABSENT)

    def test_edit_single_record_absent_to_present(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.edit_session1_url,
            {
                f"status_{self.student1.id}": "present",
                f"status_{self.student2.id}": "present",
            },
        )
        self.assertRedirects(res, self.edit_session1_url)
        self.att1_s2.refresh_from_db()
        self.assertEqual(self.att1_s2.status, Attendance.Status.PRESENT)

    def test_edit_multiple_students_simultaneously(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.edit_session1_url,
            {
                f"status_{self.student1.id}": "absent",
                f"status_{self.student2.id}": "present",
            },
        )
        self.assertRedirects(res, self.edit_session1_url)
        self.att1_s1.refresh_from_db()
        self.att1_s2.refresh_from_db()
        self.assertEqual(self.att1_s1.status, Attendance.Status.ABSENT)
        self.assertEqual(self.att1_s2.status, Attendance.Status.PRESENT)

    def test_unrelated_sessions_and_dates_remain_unchanged(self):
        self.client.force_login(self.admin)
        self.client.post(
            self.edit_session1_url,
            {
                f"status_{self.student1.id}": "absent",
                f"status_{self.student2.id}": "absent",
            },
        )
        self.att2_s1.refresh_from_db()
        self.att2_s2.refresh_from_db()
        self.assertEqual(self.att2_s1.status, Attendance.Status.PRESENT)
        self.assertEqual(self.att2_s2.status, Attendance.Status.PRESENT)

    def test_invalid_course_session_combination_returns_404(self):
        self.client.force_login(self.admin)
        invalid_url = reverse(
            "edit_attendance_session",
            kwargs={"course_id": self.other_course.id, "session_id": self.session1.id},
        )
        res = self.client.get(invalid_url)
        self.assertEqual(res.status_code, 404)

        res_post = self.client.post(
            invalid_url, {f"status_{self.student1.id}": "absent"}
        )
        self.assertEqual(res_post.status_code, 404)

    def test_invalid_attendance_status_rejected(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.edit_session1_url,
            {
                f"status_{self.student1.id}": "not_a_real_status",
            },
        )
        self.assertEqual(res.status_code, 200)
        messages_list = list(get_messages(res.wsgi_request))
        self.assertTrue(
            any("Invalid attendance status" in m.message for m in messages_list)
        )
        self.att1_s1.refresh_from_db()
        self.assertEqual(self.att1_s1.status, Attendance.Status.PRESENT)

    def test_unenrolled_student_status_rejected(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            self.edit_session1_url,
            {
                f"status_{self.unenrolled_student.id}": "present",
            },
        )
        self.assertEqual(res.status_code, 200)
        messages_list = list(get_messages(res.wsgi_request))
        self.assertTrue(
            any("is not enrolled" in m.message for m in messages_list)
        )
        self.assertFalse(
            Attendance.objects.filter(
                session=self.session1, student=self.unenrolled_student
            ).exists()
        )

    def test_no_duplicate_attendance_or_session_records_created(self):
        self.client.force_login(self.admin)
        initial_sessions_count = AttendanceSession.objects.filter(course=self.course).count()
        initial_attendance_count = Attendance.objects.filter(session=self.session1).count()

        # Submit multiple edits
        self.client.post(
            self.edit_session1_url,
            {
                f"status_{self.student1.id}": "absent",
                f"status_{self.student2.id}": "present",
            },
        )
        self.client.post(
            self.edit_session1_url,
            {
                f"status_{self.student1.id}": "present",
                f"status_{self.student2.id}": "absent",
            },
        )

        self.assertEqual(
            AttendanceSession.objects.filter(course=self.course).count(),
            initial_sessions_count,
        )
        self.assertEqual(
            Attendance.objects.filter(session=self.session1).count(),
            initial_attendance_count,
        )

    def test_picker_with_no_sessions_renders_gracefully(self):
        self.client.force_login(self.admin)
        empty_course = Course.objects.create(
            code="CS-999", name="Empty Sessions Course", semester="Semester 1"
        )
        picker_url = reverse(
            "edit_attendance_picker", kwargs={"course_id": empty_course.id}
        )
        res = self.client.get(picker_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "No attendance sessions have been recorded")

    def test_links_exist_in_course_detail_and_attendance_viewer(self):
        self.client.force_login(self.admin)
        res_detail = self.client.get(
            reverse("course_detail", kwargs={"course_id": self.course.id})
        )
        self.assertEqual(res_detail.status_code, 200)
        self.assertContains(res_detail, self.edit_picker_url)

        res_viewer = self.client.get(
            reverse("view_attendance", kwargs={"course_id": self.course.id})
        )
        self.assertEqual(res_viewer.status_code, 200)
        self.assertContains(res_viewer, self.edit_session1_url)
        self.assertContains(res_viewer, self.edit_picker_url)


class CourseNoticeBoardTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_notice@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
            first_name="Notice",
            last_name="Admin",
            must_change_password=False,
        )
        self.student1 = User.objects.create_user(
            email="student1_notice@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A501",
            first_name="Ali",
            last_name="Raza",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="student2_notice@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A502",
            first_name="Bilal",
            last_name="Tariq",
            must_change_password=False,
        )
        self.course_a = Course.objects.create(
            code="CS-301",
            name="Database Systems",
            semester="Fall 2026",
            is_active=True,
        )
        self.course_b = Course.objects.create(
            code="CS-302",
            name="Computer Networks",
            semester="Fall 2026",
            is_active=True,
        )
        Enrollment.objects.create(course=self.course_a, student=self.student1)
        Enrollment.objects.create(course=self.course_b, student=self.student2)

        self.list_url = reverse("announcement_list")
        self.create_url = reverse("create_announcement")

    def test_create_course_specific_notice_and_notifications(self):
        self.client.force_login(self.admin)
        post_data = {
            "title": "DBMS Lab Evaluation Schedule",
            "message": "Evaluation will take place next Monday during normal lab hours.",
            "course": self.course_a.id,
            "is_published": "on",
            "_has_published": "1",
        }
        res = self.client.post(self.create_url, post_data)
        self.assertEqual(res.status_code, 302)

        notice = Announcement.objects.get(title="DBMS Lab Evaluation Schedule")
        self.assertEqual(notice.course, self.course_a)
        self.assertTrue(notice.is_published)

        # Enrolled student 1 received notification
        notifs_student1 = Notification.objects.filter(recipient=self.student1, content_id=notice.id)
        self.assertEqual(notifs_student1.count(), 1)
        self.assertIn("DBMS Lab Evaluation Schedule", notifs_student1.first().title)

        # Unenrolled student 2 received NO notification
        notifs_student2 = Notification.objects.filter(recipient=self.student2, content_id=notice.id)
        self.assertEqual(notifs_student2.count(), 0)

    def test_create_general_notice_and_notifications(self):
        self.client.force_login(self.admin)
        post_data = {
            "title": "Campus Closed on Thursday",
            "message": "Campus will remain closed on Thursday due to local holiday.",
            "course": "",
            "is_published": "on",
            "_has_published": "1",
        }
        res = self.client.post(self.create_url, post_data)
        self.assertEqual(res.status_code, 302)

        notice = Announcement.objects.get(title="Campus Closed on Thursday")
        self.assertIsNone(notice.course)

        # Both students received notifications
        self.assertTrue(Notification.objects.filter(recipient=self.student1, content_id=notice.id).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.student2, content_id=notice.id).exists())

    def test_student_notice_board_isolation(self):
        Announcement.objects.create(
            title="Notice for CS-301",
            message="Content for CS-301",
            course=self.course_a,
            created_by=self.admin,
            is_published=True,
        )
        Announcement.objects.create(
            title="Notice for CS-302",
            message="Content for CS-302",
            course=self.course_b,
            created_by=self.admin,
            is_published=True,
        )
        Announcement.objects.create(
            title="Notice for Everyone",
            message="General content",
            course=None,
            created_by=self.admin,
            is_published=True,
        )

        self.client.force_login(self.student1)
        res = self.client.get(self.list_url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Notice for CS-301")
        self.assertContains(res, "Notice for Everyone")
        self.assertNotContains(res, "Notice for CS-302")

        # Student 1 accessing filter for enrolled course A
        res_a = self.client.get(f"{self.list_url}?course={self.course_a.id}")
        self.assertEqual(res_a.status_code, 200)
        self.assertContains(res_a, "Notice for CS-301")

        # Student 1 attempting to access filter for unenrolled course B gets 403 Forbidden
        res_b = self.client.get(f"{self.list_url}?course={self.course_b.id}")
        self.assertEqual(res_b.status_code, 403)

    def test_pinned_notices_ordered_first(self):
        older_pinned = Announcement.objects.create(
            title="Important Rules (Pinned)",
            message="Follow all lab rules strictly.",
            course=self.course_a,
            created_by=self.admin,
            is_pinned=True,
            is_published=True,
        )
        Announcement.objects.filter(id=older_pinned.id).update(
            created_at=timezone.now() - timedelta(days=5)
        )
        older_pinned.refresh_from_db()

        newer_regular = Announcement.objects.create(
            title="Quiz Rescheduled (Regular)",
            message="Quiz rescheduled to Friday.",
            course=self.course_a,
            created_by=self.admin,
            is_pinned=False,
            is_published=True,
        )

        self.client.force_login(self.student1)
        res = self.client.get(self.list_url)
        notices = list(res.context["announcements"])
        self.assertEqual(notices[0], older_pinned)
        self.assertEqual(notices[1], newer_regular)

    def test_unpublished_draft_hidden_from_students(self):
        draft_notice = Announcement.objects.create(
            title="Draft Exam Schedule",
            message="Secret tentative schedule.",
            course=self.course_a,
            created_by=self.admin,
            is_published=False,
        )

        # Admin can view draft in list
        self.client.force_login(self.admin)
        res_admin = self.client.get(self.list_url)
        self.assertContains(res_admin, "Draft Exam Schedule")
        self.assertContains(res_admin, "Draft (Unpublished)")

        # Student cannot view draft in list or detail
        self.client.force_login(self.student1)
        res_student = self.client.get(self.list_url)
        self.assertNotContains(res_student, "Draft Exam Schedule")

        detail_url = reverse("announcement_detail", kwargs={"announcement_id": draft_notice.id})
        res_detail = self.client.get(detail_url)
        self.assertEqual(res_detail.status_code, 404)

    def test_publishing_draft_triggers_notifications_once(self):
        draft_notice = Announcement.objects.create(
            title="Tentative Workshop",
            message="Planning a workshop.",
            course=self.course_a,
            created_by=self.admin,
            is_published=False,
        )
        self.assertEqual(Notification.objects.count(), 0)

        # Admin publishes draft
        self.client.force_login(self.admin)
        edit_url = reverse("edit_announcement", kwargs={"announcement_id": draft_notice.id})
        res_publish = self.client.post(edit_url, {
            "title": "Confirmed Workshop",
            "message": "Workshop confirmed.",
            "course": self.course_a.id,
            "is_published": "on",
            "_has_published": "1",
        })
        self.assertEqual(res_publish.status_code, 302)
        self.assertEqual(Notification.objects.filter(recipient=self.student1).count(), 1)

        # Admin edits again (does not re-notify)
        res_edit2 = self.client.post(edit_url, {
            "title": "Confirmed Workshop - Room Changed",
            "message": "Room changed to Lab 3.",
            "course": self.course_a.id,
            "is_published": "on",
            "_has_published": "1",
        })
        self.assertEqual(res_edit2.status_code, 302)
        self.assertEqual(Notification.objects.filter(recipient=self.student1).count(), 1)

    def test_notice_detail_permissions(self):
        notice_a = Announcement.objects.create(
            title="Notice Course A",
            message="Details A",
            course=self.course_a,
            created_by=self.admin,
            is_published=True,
        )
        notice_b = Announcement.objects.create(
            title="Notice Course B",
            message="Details B",
            course=self.course_b,
            created_by=self.admin,
            is_published=True,
        )

        self.client.force_login(self.student1)
        res_a = self.client.get(reverse("announcement_detail", kwargs={"announcement_id": notice_a.id}))
        self.assertEqual(res_a.status_code, 200)
        self.assertContains(res_a, "Details A")

        # Student 1 attempting to view Notice B gets 403 Forbidden
        res_b = self.client.get(reverse("announcement_detail", kwargs={"announcement_id": notice_b.id}))
        self.assertEqual(res_b.status_code, 403)


class StudyMaterialsAndLinksTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin_res@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
            first_name="Resource",
            last_name="Admin",
            must_change_password=False,
        )
        self.student1 = User.objects.create_user(
            email="student1_res@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A511",
            first_name="Hamza",
            last_name="Ali",
            must_change_password=False,
        )
        self.student2 = User.objects.create_user(
            email="student2_res@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A512",
            first_name="Usman",
            last_name="Ghani",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="CS-201",
            name="Data Structures & Algorithms",
            semester="Fall 2026",
            is_active=True,
        )
        Enrollment.objects.create(course=self.course, student=self.student1)

        self.resources_url = reverse("course_resources", kwargs={"course_id": self.course.id})
        self.upload_url = reverse("upload_course_resource", kwargs={"course_id": self.course.id})
        self.add_link_url = reverse("create_course_link", kwargs={"course_id": self.course.id})

    def test_admin_can_upload_valid_study_material(self):
        self.client.force_login(self.admin)
        dummy_file = SimpleUploadedFile("lecture1_arrays.pdf", b"%PDF-1.4 dummy pdf content", content_type="application/pdf")
        post_data = {
            "title": "Lecture 1: Arrays and Dynamic Sizing",
            "category": "lecture_notes",
            "description": "Introductory slides and complexity discussion.",
            "file": dummy_file,
            "is_published": "on",
            "_has_published": "1",
        }
        res = self.client.post(self.upload_url, post_data)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, self.resources_url)

        resource = CourseResource.objects.get(title="Lecture 1: Arrays and Dynamic Sizing")
        self.assertEqual(resource.course, self.course)
        self.assertEqual(resource.original_filename, "lecture1_arrays.pdf")
        self.assertTrue(resource.file_size > 0)
        self.assertTrue(resource.is_published)

        # Enrolled student received notification
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.student1,
                content_type="course_resource",
                content_id=resource.id,
            ).exists()
        )

    def test_upload_rejects_dangerous_file_extensions(self):
        self.client.force_login(self.admin)
        for bad_name in ["malware.exe", "script.sh", "backdoor.php", "hack.bat"]:
            bad_file = SimpleUploadedFile(bad_name, b"binary content", content_type="application/octet-stream")
            res = self.client.post(self.upload_url, {
                "title": f"Test {bad_name}",
                "category": "other",
                "file": bad_file,
            })
            self.assertEqual(res.status_code, 200)
            self.assertIn("file", res.context["form"].errors)

    def test_upload_rejects_oversized_file(self):
        self.client.force_login(self.admin)
        large_size = 26 * 1024 * 1024
        large_file = SimpleUploadedFile("huge_file.pdf", b"0" * large_size, content_type="application/pdf")

        res = self.client.post(self.upload_url, {
            "title": "Too Large File",
            "category": "book",
            "file": large_file,
        })
        self.assertEqual(res.status_code, 200)
        self.assertIn("file", res.context["form"].errors)

    def test_study_material_download_permissions(self):
        dummy_file = SimpleUploadedFile("dsa_cheatsheet.pdf", b"%PDF-1.4 sample content", content_type="application/pdf")
        resource = CourseResource.objects.create(
            course=self.course,
            title="DSA Cheatsheet",
            category="study_guide",
            file=dummy_file,
            original_filename="dsa_cheatsheet.pdf",
            file_size=len(b"%PDF-1.4 sample content"),
            uploaded_by=self.admin,
            is_published=True,
        )
        download_url = reverse("download_course_resource", kwargs={"resource_id": resource.id})

        # 1. Enrolled student can download
        self.client.force_login(self.student1)
        res_student1 = self.client.get(download_url)
        self.assertEqual(res_student1.status_code, 200)
        self.assertEqual(res_student1["Content-Type"], "application/pdf")

        # 2. Unenrolled student gets 403 Forbidden
        self.client.force_login(self.student2)
        res_student2 = self.client.get(download_url)
        self.assertEqual(res_student2.status_code, 403)

        # 3. Unauthenticated user gets redirected to login
        self.client.logout()
        res_anon = self.client.get(download_url)
        self.assertEqual(res_anon.status_code, 302)
        self.assertIn("/login", res_anon.url)

    def test_study_material_edit_and_delete(self):
        dummy_file = SimpleUploadedFile("notes.pdf", b"%PDF-1.4 notes", content_type="application/pdf")
        resource = CourseResource.objects.create(
            course=self.course,
            title="Lecture Notes",
            category="lecture_notes",
            file=dummy_file,
            original_filename="notes.pdf",
            file_size=15,
            uploaded_by=self.admin,
            is_published=True,
        )
        edit_url = reverse("edit_course_resource", kwargs={"resource_id": resource.id})
        delete_url = reverse("delete_course_resource", kwargs={"resource_id": resource.id})

        self.client.force_login(self.admin)
        res_edit = self.client.post(edit_url, {
            "title": "Lecture Notes - Revised",
            "category": "lecture_notes",
            "description": "Updated notes.",
            "is_published": "on",
            "_has_published": "1",
        })
        self.assertEqual(res_edit.status_code, 302)
        resource.refresh_from_db()
        self.assertEqual(resource.title, "Lecture Notes - Revised")

        # Delete
        res_delete = self.client.post(delete_url)
        self.assertEqual(res_delete.status_code, 302)
        self.assertFalse(CourseResource.objects.filter(id=resource.id).exists())

    def test_useful_link_lifecycle_and_validation(self):
        self.client.force_login(self.admin)
        # 1. Reject invalid URLs
        res_invalid = self.client.post(self.add_link_url, {
            "title": "Bad Link",
            "category": "other",
            "url": "javascript:alert(1)",
        })
        self.assertEqual(res_invalid.status_code, 200)
        self.assertIn("url", res_invalid.context["form"].errors)

        # 2. Add valid link
        res_valid = self.client.post(self.add_link_url, {
            "title": "Official Course WhatsApp Group",
            "category": "whatsapp",
            "url": "https://chat.whatsapp.com/samplegroup123",
            "is_published": "on",
            "_has_published": "1",
        })
        self.assertEqual(res_valid.status_code, 302)
        link = CourseLink.objects.get(title="Official Course WhatsApp Group")
        self.assertEqual(link.course, self.course)
        self.assertEqual(link.category, "whatsapp")

        # Notification created
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.student1,
                content_type="course_link",
                content_id=link.id,
            ).exists()
        )

        # 3. Edit link
        edit_link_url = reverse("edit_course_link", kwargs={"link_id": link.id})
        res_edit = self.client.post(edit_link_url, {
            "title": "Official Course WhatsApp Group - Section A",
            "category": "whatsapp",
            "url": "https://chat.whatsapp.com/samplegroup123",
            "is_published": "on",
            "_has_published": "1",
        })
        self.assertEqual(res_edit.status_code, 302)
        link.refresh_from_db()
        self.assertEqual(link.title, "Official Course WhatsApp Group - Section A")

        # 4. Delete link
        delete_link_url = reverse("delete_course_link", kwargs={"link_id": link.id})
        res_del = self.client.post(delete_link_url)
        self.assertEqual(res_del.status_code, 302)
        self.assertFalse(CourseLink.objects.filter(id=link.id).exists())


class InPortalNotificationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            email="student_notif@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A521",
            first_name="Saad",
            last_name="Ahmed",
            must_change_password=False,
        )
        self.course = Course.objects.create(
            code="CS-205",
            name="Operating Systems",
            semester="Fall 2026",
            is_active=True,
        )
        Enrollment.objects.create(course=self.course, student=self.student)
        self.notif_list_url = reverse("notification_list")

    def test_notification_deduplication_constraint(self):
        notif1 = Notification(
            recipient=self.student,
            course=self.course,
            title="Notice 1",
            message="Content 1",
            target_url="/announcements/1/",
            content_type="announcement",
            content_id=101,
        )
        notif2 = Notification(
            recipient=self.student,
            course=self.course,
            title="Notice 1 duplicate",
            message="Content 1 duplicate",
            target_url="/announcements/1/",
            content_type="announcement",
            content_id=101,
        )
        Notification.objects.bulk_create([notif1, notif2], ignore_conflicts=True)
        self.assertEqual(Notification.objects.filter(recipient=self.student, content_id=101).count(), 1)

    def test_notification_list_and_context_processor(self):
        Notification.objects.create(
            recipient=self.student,
            course=self.course,
            title="Test Notice 1",
            message="Message 1",
            target_url="/announcements/1/",
            content_type="announcement",
            content_id=201,
        )
        Notification.objects.create(
            recipient=self.student,
            course=self.course,
            title="Test Notice 2",
            message="Message 2",
            target_url="/announcements/2/",
            content_type="announcement",
            content_id=202,
        )

        self.client.force_login(self.student)
        res = self.client.get(self.notif_list_url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["unread_notifications_count"], 2)
        self.assertContains(res, "Test Notice 1")
        self.assertContains(res, "Test Notice 2")

    def test_mark_notification_read_and_safe_redirect(self):
        notif = Notification.objects.create(
            recipient=self.student,
            course=self.course,
            title="Target Notice",
            message="Read me",
            target_url="/courses/1/resources/",
            content_type="announcement",
            content_id=301,
        )
        self.assertFalse(notif.is_read)

        self.client.force_login(self.student)
        read_url = reverse("mark_notification_read", kwargs={"notification_id": notif.id})
        res = self.client.get(read_url)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, "/courses/1/resources/")

        notif.refresh_from_db()
        self.assertTrue(notif.is_read)

    def test_mark_notification_read_open_redirect_defense(self):
        notif = Notification.objects.create(
            recipient=self.student,
            course=self.course,
            title="Phishing Notice",
            message="Malicious target",
            target_url="https://evil-attacker.com/steal-creds",
            content_type="announcement",
            content_id=401,
        )

        self.client.force_login(self.student)
        read_url = reverse("mark_notification_read", kwargs={"notification_id": notif.id})
        res = self.client.get(read_url)
        self.assertEqual(res.status_code, 302)
        self.assertNotIn("evil-attacker.com", res.url)
        self.assertEqual(res.url, self.notif_list_url)

    def test_mark_all_notifications_read(self):
        for i in range(3):
            Notification.objects.create(
                recipient=self.student,
                course=self.course,
                title=f"Bulk Notice {i}",
                message=f"Message {i}",
                target_url="/announcements/",
                content_type="announcement",
                content_id=500 + i,
            )

        self.assertEqual(Notification.objects.filter(recipient=self.student, is_read=False).count(), 3)
        self.client.force_login(self.student)
        mark_all_url = reverse("mark_all_notifications_read")
        res = self.client.post(mark_all_url)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, self.notif_list_url)

        self.assertEqual(Notification.objects.filter(recipient=self.student, is_read=False).count(), 0)
