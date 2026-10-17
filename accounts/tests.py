import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from academics.models import Course, Enrollment, Announcement
from accounts.models import PasswordResetCode


User = get_user_model()


class UnifiedNavigationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="cr_admin@pucit.edu.pk",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            first_name="Ali",
            last_name="Raza",
            must_change_password=False,
        )
        self.student = User.objects.create_user(
            email="student@pucit.edu.pk",
            password="StudentPassword123!",
            role=User.Role.STUDENT,
            student_id="BITF24A501",
            first_name="Fatima",
            last_name="Zahra",
            must_change_password=False,
        )
        self.course_enrolled = Course.objects.create(
            code="CS-301",
            name="Operating Systems",
            semester="Semester 5",
            is_active=True,
        )
        self.course_other = Course.objects.create(
            code="CS-302",
            name="Database Systems",
            semester="Semester 5",
            is_active=True,
        )
        Enrollment.objects.create(student=self.student, course=self.course_enrolled)
        Announcement.objects.create(
            title="Midterm Schedule Released",
            message="Check the datesheet on the portal.",
            created_by=self.admin,
        )

    def test_login_page_renders_with_unified_branding(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PUCIT Class CMS")
        self.assertContains(response, "Sign in to your account")
        self.assertContains(response, "University Email")

    def test_login_and_logout_flow(self):
        # Successful login redirects to dashboard
        login_res = self.client.post(
            reverse("login"),
            {"username": self.admin.email, "password": "AdminPassword123!"},
        )
        self.assertEqual(login_res.status_code, 302)
        self.assertRedirects(login_res, reverse("dashboard"))

        # Access dashboard as logged in user
        dash_res = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_res.status_code, 200)
        self.assertContains(dash_res, "CR / Admin")

        # Logout redirects to login
        logout_res = self.client.post(reverse("logout"))
        self.assertEqual(logout_res.status_code, 302)
        self.assertRedirects(logout_res, reverse("login"))

    def test_first_login_password_change_middleware(self):
        new_student = User.objects.create_user(
            email="newstudent@pucit.edu.pk",
            password="TempPassword123!",
            role=User.Role.STUDENT,
            student_id="BITF24A999",
            must_change_password=True,
        )
        self.client.force_login(new_student)

        # Accessing dashboard is intercepted and redirected to password_change
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("password_change"))

        # Password change page renders
        pw_res = self.client.get(reverse("password_change"))
        self.assertEqual(pw_res.status_code, 200)
        self.assertContains(pw_res, "Set Your Private Password")

        # Submit password change
        change_res = self.client.post(reverse("password_change"), {
            "old_password": "TempPassword123!",
            "new_password1": "NewSecurePass123!",
            "new_password2": "NewSecurePass123!",
        })
        self.assertEqual(change_res.status_code, 302)
        self.assertRedirects(change_res, reverse("dashboard"))

        new_student.refresh_from_db()
        self.assertFalse(new_student.must_change_password)

    def test_admin_dashboard_navigation_and_links(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)

        # User and role verification in topbar
        self.assertContains(res, "CR / Admin")
        self.assertContains(res, self.admin.email)

        # Administrative navigation links in sidebar
        self.assertContains(res, reverse("dashboard"))
        self.assertContains(res, reverse("import_enrollments"))
        self.assertContains(res, reverse("import_attendance"))
        self.assertContains(res, reverse("password_change"))
        self.assertContains(res, reverse("logout"))

        # Both courses visible to admin
        self.assertContains(res, "CS-301")
        self.assertContains(res, "CS-302")
        self.assertContains(res, reverse("course_detail", kwargs={"course_id": self.course_enrolled.id}))
        self.assertContains(res, reverse("mark_attendance", kwargs={"course_id": self.course_enrolled.id}))

        # Announcement section visible
        self.assertContains(res, "Midterm Schedule Released")

        # Verify all navigation links resolve cleanly
        for url in [
            reverse("dashboard"),
            reverse("import_enrollments"),
            reverse("import_attendance"),
            reverse("password_change"),
            reverse("course_detail", kwargs={"course_id": self.course_enrolled.id}),
            reverse("mark_attendance", kwargs={"course_id": self.course_enrolled.id}),
        ]:
            route_res = self.client.get(url)
            self.assertEqual(route_res.status_code, 200, f"Route {url} failed with {route_res.status_code}")

    def test_student_dashboard_navigation_and_links(self):
        self.client.force_login(self.student)
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)

        # Student role and ID badge verification
        self.assertContains(res, "Student")
        self.assertContains(res, self.student.student_id)

        # Student navigation items present
        self.assertContains(res, reverse("dashboard"))
        self.assertContains(res, reverse("password_change"))
        self.assertContains(res, reverse("logout"))

        # Administrative links MUST NOT be present
        self.assertNotContains(res, reverse("import_enrollments"))
        self.assertNotContains(res, reverse("import_attendance"))
        self.assertNotContains(res, "Mark Attendance")

        # Enrolled course is visible
        self.assertContains(res, "CS-301")
        # Unenrolled course is NOT visible
        self.assertNotContains(res, "CS-302")

    def test_student_cannot_access_restricted_pages_directly(self):
        self.client.force_login(self.student)

        # Restricted import and attendance URLs redirect student to login
        restricted_urls = [
            reverse("import_attendance"),
            reverse("import_enrollments"),
            reverse("mark_attendance", kwargs={"course_id": self.course_enrolled.id}),
        ]
        for url in restricted_urls:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 302, f"Student accessed restricted URL {url}")
            self.assertIn("/login", res.url)

        # Student cannot access unenrolled course detail (returns 404)
        unenrolled_res = self.client.get(
            reverse("course_detail", kwargs={"course_id": self.course_other.id})
        )
        self.assertEqual(unenrolled_res.status_code, 404)

    def test_course_detail_navigation_and_access(self):
        # 1. Admin visits course detail
        self.client.force_login(self.admin)
        res_admin = self.client.get(
            reverse("course_detail", kwargs={"course_id": self.course_enrolled.id})
        )
        self.assertEqual(res_admin.status_code, 200)
        self.assertContains(res_admin, reverse("mark_attendance", kwargs={"course_id": self.course_enrolled.id}))
        self.assertContains(res_admin, reverse("import_attendance"))
        self.assertContains(res_admin, reverse("import_enrollments"))
        self.assertContains(res_admin, reverse("dashboard"))

        # 2. Student visits course detail for enrolled course
        self.client.force_login(self.student)
        res_student = self.client.get(
            reverse("course_detail", kwargs={"course_id": self.course_enrolled.id})
        )
        self.assertEqual(res_student.status_code, 200)
        self.assertContains(res_student, "CS-301")
        self.assertNotContains(res_student, "Mark Manual Attendance")
        self.assertNotContains(res_student, reverse("import_attendance"))
        self.assertNotContains(res_student, reverse("import_enrollments"))


class RollNumberLoginTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            email="student1@pucit.edu.pk",
            password="StudentPassword123!",
            role=User.Role.STUDENT,
            student_id="BITF24A001",
            first_name="Ayesha",
            last_name="Saleem",
            must_change_password=False,
        )
        self.admin = User.objects.create_user(
            email="admin@pucit.edu.pk",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            first_name="Admin",
            last_name="User",
            must_change_password=False,
        )
        self.inactive_student = User.objects.create_user(
            email="inactive@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A999",
            is_active=False,
        )

    def test_student_login_with_roll_number(self):
        res = self.client.post(
            reverse("login"),
            {"username": "BITF24A001", "password": "StudentPassword123!"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.student.id)

    def test_student_login_with_lowercase_and_whitespace(self):
        res = self.client.post(
            reverse("login"),
            {"username": "  bitf24a001  ", "password": "StudentPassword123!"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("dashboard"))

    def test_student_login_with_email_also_works(self):
        res = self.client.post(
            reverse("login"),
            {"username": "student1@pucit.edu.pk", "password": "StudentPassword123!"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("dashboard"))

    def test_admin_email_login_works(self):
        res = self.client.post(
            reverse("login"),
            {"username": "admin@pucit.edu.pk", "password": "AdminPassword123!"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.admin.id)

    def test_invalid_password_rejected(self):
        res = self.client.post(
            reverse("login"),
            {"username": "BITF24A001", "password": "WrongPassword!"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Invalid")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unknown_roll_number_rejected(self):
        res = self.client.post(
            reverse("login"),
            {"username": "BITF24A998", "password": "AnyPassword123!"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Invalid")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inactive_student_cannot_login(self):
        res = self.client.post(
            reverse("login"),
            {"username": "BITF24A999", "password": "Password123!"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Invalid")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_first_login_password_change_enforced_with_roll_number_login(self):
        self.student.must_change_password = True
        self.student.save()
        res = self.client.post(
            reverse("login"),
            {"username": "BITF24A001", "password": "StudentPassword123!"},
        )
        self.assertEqual(res.status_code, 302)
        dash_res = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_res.status_code, 302)
        self.assertRedirects(dash_res, reverse("password_change"))


class PasswordResetWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(
            email="student@pucit.edu.pk",
            password="OldPassword123!",
            role=User.Role.STUDENT,
            student_id="BITF24A100",
            first_name="Fatima",
            last_name="Ali",
            must_change_password=True,
        )
        self.admin = User.objects.create_user(
            email="cr@pucit.edu.pk",
            password="AdminOldPassword123!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def test_forgot_password_page_renders_with_link_on_login(self):
        login_res = self.client.get(reverse("login"))
        self.assertEqual(login_res.status_code, 200)
        self.assertContains(login_res, reverse("forgot_password"))

        forgot_res = self.client.get(reverse("forgot_password"))
        self.assertEqual(forgot_res.status_code, 200)
        self.assertContains(forgot_res, "Password Recovery")

    def test_request_code_using_student_roll_number(self):
        mail.outbox.clear()
        res = self.client.post(
            reverse("forgot_password"),
            {"identifier": "BITF24A100"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("verify_reset_code"))

        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertIn("student@pucit.edu.pk", sent_email.to)
        self.assertIn("verification code is", sent_email.body)

        match = re.search(r"\b(\d{6})\b", sent_email.body)
        self.assertTrue(match)
        raw_code = match.group(1)

        code_obj = PasswordResetCode.objects.filter(user=self.student).first()
        self.assertIsNotNone(code_obj)
        self.assertTrue(code_obj.check_code(raw_code))
        self.assertFalse(code_obj.is_consumed)

    def test_request_code_using_admin_email(self):
        mail.outbox.clear()
        res = self.client.post(
            reverse("forgot_password"),
            {"identifier": "cr@pucit.edu.pk"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("cr@pucit.edu.pk", mail.outbox[0].to)

    def test_unknown_identifier_returns_generic_response(self):
        mail.outbox.clear()
        res = self.client.post(
            reverse("forgot_password"),
            {"identifier": "UNKNOWN999"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("verify_reset_code"))
        self.assertEqual(len(mail.outbox), 0)

    def test_full_reset_workflow_success(self):
        mail.outbox.clear()
        # 1. Request code
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        raw_code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)

        # 2. Verify code
        verify_res = self.client.post(
            reverse("verify_reset_code"),
            {"code": raw_code},
        )
        self.assertEqual(verify_res.status_code, 302)
        self.assertRedirects(verify_res, reverse("reset_password"))

        # 3. Set new password
        reset_res = self.client.post(
            reverse("reset_password"),
            {"new_password1": "BrandNewSecurePassword123!", "new_password2": "BrandNewSecurePassword123!"},
        )
        self.assertEqual(reset_res.status_code, 302)
        self.assertRedirects(reset_res, reverse("login"))

        # 4. Verify password changed in DB
        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("BrandNewSecurePassword123!"))
        self.assertFalse(self.student.must_change_password)

        # 5. Student can now log in with roll number and new password
        login_res = self.client.post(
            reverse("login"),
            {"username": "BITF24A100", "password": "BrandNewSecurePassword123!"},
        )
        self.assertEqual(login_res.status_code, 302)
        self.assertRedirects(login_res, reverse("dashboard"))

    def test_invalid_code_rejected_and_attempts_counted(self):
        mail.outbox.clear()
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})

        res = self.client.post(
            reverse("verify_reset_code"),
            {"code": "000000"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Incorrect verification code")
        code_obj = PasswordResetCode.objects.get(user=self.student, is_consumed=False)
        self.assertEqual(code_obj.attempts_count, 1)

    def test_excessive_incorrect_attempts_blocks_code(self):
        mail.outbox.clear()
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        code_obj = PasswordResetCode.objects.get(user=self.student, is_consumed=False)
        code_obj.attempts_count = 5
        code_obj.save()

        res = self.client.post(
            reverse("verify_reset_code"),
            {"code": "123456"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Maximum verification attempts exceeded")

    def test_expired_code_rejected(self):
        mail.outbox.clear()
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        code_obj = PasswordResetCode.objects.get(user=self.student, is_consumed=False)
        code_obj.expires_at = timezone.now() - timedelta(minutes=1)
        code_obj.save()

        res = self.client.post(
            reverse("verify_reset_code"),
            {"code": "123456"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "expired")

    def test_new_code_request_invalidates_previous_code(self):
        mail.outbox.clear()
        # Request 1
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        code1 = PasswordResetCode.objects.filter(user=self.student).first()
        raw_code1 = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)

        code1.created_at = timezone.now() - timedelta(seconds=70)
        code1.save()

        # Request 2
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        code1.refresh_from_db()
        self.assertTrue(code1.is_consumed)

        code2 = PasswordResetCode.objects.filter(user=self.student, is_consumed=False).first()
        self.assertFalse(code2.check_code(raw_code1))

    def test_used_code_cannot_be_reused(self):
        mail.outbox.clear()
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        raw_code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)

        self.client.post(reverse("verify_reset_code"), {"code": raw_code})
        self.client.post(
            reverse("reset_password"),
            {"new_password1": "PasswordOne123!", "new_password2": "PasswordOne123!"},
        )

        code_obj = PasswordResetCode.objects.get(user=self.student)
        self.assertTrue(code_obj.is_consumed)
        self.assertFalse(code_obj.is_valid())

    def test_email_delivery_failure_does_not_create_usable_code(self):
        with patch("accounts.views.send_mail", side_effect=Exception("SMTP Connection Error")):
            res = self.client.post(
                reverse("forgot_password"),
                {"identifier": "BITF24A100"},
            )
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "Unable to deliver")
            self.assertFalse(PasswordResetCode.objects.filter(user=self.student, is_consumed=False).exists())

    def test_student_forced_password_change_middleware(self):
        self.student.must_change_password = True
        self.student.save(update_fields=["must_change_password"])
        self.client.force_login(self.student)

        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("password_change"))

    def test_static_files_bypass_password_change_middleware(self):
        self.student.must_change_password = True
        self.student.save(update_fields=["must_change_password"])
        self.client.force_login(self.student)

        # Static assets should not be redirected
        res = self.client.get("/static/css/style.css")
        # Either 200 (if found) or 404 (if not found in test env), but definitely not 302 redirect to password_change
        self.assertNotEqual(res.status_code, 302)

    def test_ip_rate_limiting_forgot_password(self):
        cache.clear()
        # Send 5 requests from the same IP (client default 127.0.0.1)
        for i in range(5):
            res = self.client.post(reverse("forgot_password"), {"identifier": f"unknown{i}@pucit.edu.pk"})
            self.assertEqual(res.status_code, 302)

        # 6th request from the same IP is blocked by rate limit
        res6 = self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        self.assertEqual(res6.status_code, 200)
        self.assertContains(res6, "Too many password reset requests from your network")

    def test_is_recent_in_new_session_assigns_dummy_token_without_loop(self):
        cache.clear()
        # Session 1 requests code
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})

        # Session 2 (fresh client with no session cookies) requests reset for same user within 60s
        from django.test import Client
        fresh_client = Client()
        res = fresh_client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("verify_reset_code"))

        # Navigating to verify_reset_code in fresh_client works without redirect loop
        verify_res = fresh_client.get(reverse("verify_reset_code"))
        self.assertEqual(verify_res.status_code, 200)
        self.assertContains(verify_res, "Enter Verification Code")

    def test_max_attempts_immediately_consumes_code(self):
        cache.clear()
        self.client.post(reverse("forgot_password"), {"identifier": "BITF24A100"})
        code_obj = PasswordResetCode.objects.filter(user=self.student, is_consumed=False).first()

        for _ in range(5):
            self.client.post(reverse("verify_reset_code"), {"code": "000000"})

        code_obj.refresh_from_db()
        self.assertTrue(code_obj.is_consumed)
        self.assertEqual(code_obj.attempts_count, 5)


