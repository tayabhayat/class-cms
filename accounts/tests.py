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
from accounts.models import PasswordResetAudit, PasswordResetCode


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


class ForgotPasswordContactAdminTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            email="student@pucit.edu.pk",
            password="StudentPass123!",
            role=User.Role.STUDENT,
            student_id="BITF24A100",
        )

    def test_login_page_has_link_to_forgot_password(self):
        res = self.client.get(reverse("login"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, reverse("forgot_password"))

    def test_forgot_password_page_instructs_student_to_contact_admin(self):
        res = self.client.get(reverse("forgot_password"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Password Recovery Assistance")
        self.assertContains(res, "Class Representative (CR)")
        self.assertContains(res, "Course Administrator")
        self.assertContains(res, reverse("login"))

    def test_legacy_verify_and_reset_endpoints_redirect_to_forgot_password(self):
        for route in ["verify_reset_code", "reset_password"]:
            res = self.client.get(reverse(route))
            self.assertEqual(res.status_code, 302)
            self.assertRedirects(res, reverse("forgot_password"))


class AdminAssistedPasswordResetTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="cr@pucit.edu.pk",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            first_name="Hassan",
            last_name="Raza",
            must_change_password=False,
        )
        self.student = User.objects.create_user(
            email="student1@pucit.edu.pk",
            password="OldStudentPassword123!",
            role=User.Role.STUDENT,
            student_id="BITF24A001",
            first_name="Ayesha",
            last_name="Saleem",
            must_change_password=False,
        )
        self.other_admin = User.objects.create_user(
            email="other_admin@pucit.edu.pk",
            password="OtherAdminPassword123!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def test_only_authorized_admin_can_access_student_list(self):
        # 1. Anonymous user redirected to login
        res = self.client.get(reverse("admin_student_list"))
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

        # 2. Student user redirected to login (unauthorized)
        self.client.force_login(self.student)
        res = self.client.get(reverse("admin_student_list"))
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

        # 3. Admin user successfully accesses list
        self.client.force_login(self.admin)
        res = self.client.get(reverse("admin_student_list"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "BITF24A001")
        self.assertContains(res, "Ayesha Saleem")
        self.assertContains(res, reverse("admin_reset_student_password", kwargs={"student_id": self.student.id}))

    def test_student_list_search_by_roll_number_and_name(self):
        self.client.force_login(self.admin)
        # Search by roll number
        res = self.client.get(reverse("admin_student_list"), {"q": "BITF24A001"})
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Ayesha Saleem")

        # Search with non-matching term
        res_empty = self.client.get(reverse("admin_student_list"), {"q": "NONEXISTENT"})
        self.assertEqual(res_empty.status_code, 200)
        self.assertContains(res_empty, "No student accounts found")

    def test_admin_can_view_reset_confirmation_page_without_exposing_old_password(self):
        self.client.force_login(self.admin)
        confirm_url = reverse("admin_reset_student_password", kwargs={"student_id": self.student.id})
        res = self.client.get(confirm_url)
        self.assertEqual(res.status_code, 200)
        self.assertTemplateUsed(res, "accounts/admin_reset_password_confirm.html")
        self.assertContains(res, "BITF24A001")
        self.assertContains(res, "Ayesha Saleem")
        # Ensure old password plaintext and hash are NOT exposed
        self.assertNotContains(res, "OldStudentPassword123!")
        self.assertNotContains(res, self.student.password)

    def test_admin_reset_password_generates_temp_password_and_sets_must_change(self):
        self.client.force_login(self.admin)
        confirm_url = reverse("admin_reset_student_password", kwargs={"student_id": self.student.id})

        # POST to confirm reset
        res = self.client.post(confirm_url)
        self.assertEqual(res.status_code, 302)
        self.assertRedirects(res, reverse("admin_reset_student_password_done"), fetch_redirect_response=False)

        # Verify target user was updated in DB
        self.student.refresh_from_db()
        self.assertTrue(self.student.must_change_password)
        # Old password no longer works
        self.assertFalse(self.student.check_password("OldStudentPassword123!"))

        # Follow redirect to done page: temporary password displayed
        done_res = self.client.get(reverse("admin_reset_student_password_done"))
        self.assertEqual(done_res.status_code, 200)
        self.assertContains(done_res, "Password Reset Successful")
        self.assertContains(done_res, "One-Time Temporary Password")

        # Extract temporary password from the view context
        temp_password = done_res.context["reset_data"]["temp_password"]
        self.assertTrue(len(temp_password) >= 12)
        # Verify student password is saved using Django's password hashing
        self.assertTrue(self.student.check_password(temp_password))

        # Test temporary password displayed ONLY ONCE:
        # Refreshing or visiting again clears it
        second_res = self.client.get(reverse("admin_reset_student_password_done"))
        self.assertEqual(second_res.status_code, 302)
        self.assertRedirects(second_res, reverse("admin_student_list"))

        # Test student login with temporary password and enforced password change
        self.client.logout()
        login_res = self.client.post(reverse("login"), {
            "username": "BITF24A001",
            "password": temp_password,
        })
        self.assertEqual(login_res.status_code, 302)
        self.assertEqual(login_res.url, reverse("dashboard"))

        # Dashboard access is intercepted by middleware forcing password change
        dash_res = self.client.get(reverse("dashboard"))
        self.assertEqual(dash_res.status_code, 302)
        self.assertRedirects(dash_res, reverse("password_change"))

    def test_students_cannot_initiate_or_confirm_reset(self):
        self.client.force_login(self.student)
        confirm_url = reverse("admin_reset_student_password", kwargs={"student_id": self.student.id})
        # GET blocked
        res_get = self.client.get(confirm_url)
        self.assertEqual(res_get.status_code, 302)
        self.assertIn("/login", res_get.url)

        # POST blocked
        res_post = self.client.post(confirm_url)
        self.assertEqual(res_post.status_code, 302)
        self.assertIn("/login", res_post.url)

    def test_invalid_targets_rejected(self):
        self.client.force_login(self.admin)
        # Non-existent student returns 404
        bad_url = reverse("admin_reset_student_password", kwargs={"student_id": 99999})
        res_404 = self.client.get(bad_url)
        self.assertEqual(res_404.status_code, 404)

        # Cannot reset another admin
        admin_target_url = reverse("admin_reset_student_password", kwargs={"student_id": self.other_admin.id})
        res_admin = self.client.get(admin_target_url)
        self.assertEqual(res_admin.status_code, 302)
        self.assertRedirects(res_admin, reverse("admin_student_list"))

    def test_audit_record_created_without_passwords_or_secrets(self):
        self.client.force_login(self.admin)
        confirm_url = reverse("admin_reset_student_password", kwargs={"student_id": self.student.id})
        self.client.post(confirm_url)

        audit = PasswordResetAudit.objects.filter(target_user=self.student).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.reset_by, self.admin)
        self.assertEqual(audit.target_user, self.student)
        self.assertIsNotNone(audit.timestamp)

        # Verify audit model attributes do not contain password or hash
        audit_fields = [f.name for f in PasswordResetAudit._meta.get_fields()]
        self.assertNotIn("password", audit_fields)
        self.assertNotIn("temp_password", audit_fields)
        self.assertNotIn("code_hash", audit_fields)

    def test_temporary_password_done_has_no_store_headers_and_clipboard_elements(self):
        self.client.force_login(self.admin)
        confirm_url = reverse("admin_reset_student_password", kwargs={"student_id": self.student.id})
        self.client.post(confirm_url)

        done_res = self.client.get(reverse("admin_reset_student_password_done"))
        self.assertEqual(done_res.status_code, 200)

        # Check Cache-Control: no-store and Pragma: no-cache headers
        cache_control = done_res.headers.get("Cache-Control", "")
        self.assertIn("no-store", cache_control)
        self.assertIn("no-cache", cache_control)
        self.assertEqual(done_res.headers.get("Pragma"), "no-cache")

        # Check clipboard and manual copy fallback elements in template
        self.assertContains(done_res, "copyPasswordBtn")
        self.assertContains(done_res, "copyFeedback")
        self.assertContains(done_res, "tempPasswordDisplay")
        self.assertContains(done_res, "copyTemporaryPassword")
        self.assertContains(done_res, "fallbackCopy")
        self.assertContains(done_res, "selectPasswordText")

    def test_reset_rolls_back_when_audit_creation_fails(self):
        self.client.force_login(self.admin)
        confirm_url = reverse("admin_reset_student_password", kwargs={"student_id": self.student.id})

        # Mock PasswordResetAudit.objects.create to simulate DB failure
        with patch.object(PasswordResetAudit.objects, "create", side_effect=Exception("Database audit creation failed")):
            with self.assertRaises(Exception):
                self.client.post(confirm_url)

        # Verify target student state was rolled back completely
        self.student.refresh_from_db()
        # Old password must still be valid
        self.assertTrue(self.student.check_password("OldStudentPassword123!"))
        # must_change_password must NOT be True
        self.assertFalse(self.student.must_change_password)
        # No audit record should exist
        self.assertFalse(PasswordResetAudit.objects.filter(target_user=self.student).exists())
        # Temporary password must not be in session
        self.assertNotIn("admin_temp_password_display", self.client.session)


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
        self.assertNotEqual(res.status_code, 302)


class PasswordShowHideEyeButtonTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            email="student_eye@pucit.edu.pk",
            password="EyePassword123!",
            role=User.Role.STUDENT,
            student_id="BITF24A555",
            must_change_password=False,
        )

    def test_login_page_renders_eye_button(self):
        res = self.client.get(reverse("login"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "password-input-wrapper")
        self.assertContains(res, "password-toggle-btn")
        self.assertContains(res, 'type="button"')
        self.assertContains(res, 'aria-label="Show password"')

    def test_password_change_page_renders_eye_button_for_all_password_fields(self):
        self.client.force_login(self.student)
        res = self.client.get(reverse("password_change"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "password-input-wrapper")
        self.assertContains(res, "password-toggle-btn")
        # 3 password inputs (old, new1, new2) each have a toggle button
        self.assertEqual(res.content.decode().count('<button type="button" class="password-toggle-btn"'), 3)


class UserActivityMiddlewareTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            email="activity_student@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A701",
            must_change_password=False,
        )
        self.admin = User.objects.create_user(
            email="activity_admin@pucit.edu.pk",
            password="Password123!",
            role=User.Role.ADMIN,
            must_change_password=False,
        )

    def test_anonymous_request_does_not_update_activity(self):
        res = self.client.get(reverse("login"))
        self.assertEqual(res.status_code, 200)
        self.student.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertIsNone(self.student.last_activity)
        self.assertIsNone(self.admin.last_activity)

    def test_authenticated_request_updates_last_activity(self):
        self.client.force_login(self.student)
        self.assertIsNone(self.student.last_activity)

        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)

        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.last_activity)
        self.assertAlmostEqual(
            self.student.last_activity.timestamp(),
            timezone.now().timestamp(),
            delta=5,
        )

    def test_subsequent_request_within_throttle_window_is_throttled(self):
        self.client.force_login(self.student)
        self.client.get(reverse("dashboard"))
        self.student.refresh_from_db()
        first_activity = self.student.last_activity

        # Immediate second request
        self.client.get(reverse("dashboard"))
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_activity, first_activity)

    def test_request_after_throttle_interval_updates_last_activity(self):
        self.client.force_login(self.student)
        self.client.get(reverse("dashboard"))
        self.student.refresh_from_db()
        first_activity = self.student.last_activity

        # Simulate that the previous update occurred 75 seconds ago
        past_time = timezone.now() - timedelta(seconds=75)
        User.objects.filter(id=self.student.id).update(last_activity=past_time)

        # Update the session throttle timestamp to match past time
        session = self.client.session
        session["last_activity_update_ts"] = past_time.timestamp()
        session.save()

        # Send new request
        self.client.get(reverse("dashboard"))
        self.student.refresh_from_db()
        self.assertGreater(self.student.last_activity, past_time)
        self.assertAlmostEqual(
            self.student.last_activity.timestamp(),
            timezone.now().timestamp(),
            delta=5,
        )

    def test_static_asset_requests_bypass_activity_tracking(self):
        self.client.force_login(self.student)
        res = self.client.get("/static/css/style.css")
        self.student.refresh_from_db()
        self.assertIsNone(self.student.last_activity)

    def test_throttle_works_reliably_across_multiple_sessions(self):
        from django.test import Client

        client1 = Client()
        client2 = Client()

        client1.force_login(self.student)
        client2.force_login(self.student)

        # 1. Request from Session 1 updates last_activity
        client1.get(reverse("dashboard"))
        self.student.refresh_from_db()
        t1 = self.student.last_activity
        self.assertIsNotNone(t1)

        # 2. Request from Session 2 immediately afterwards is throttled without DB update
        client2.get(reverse("dashboard"))
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_activity, t1)

        # 3. Simulate passage of time beyond 60s
        past_time = timezone.now() - timedelta(seconds=80)
        User.objects.filter(id=self.student.id).update(last_activity=past_time)

        # 4. Session 2 makes a request: updates last_activity
        client2.get(reverse("dashboard"))
        self.student.refresh_from_db()
        t2 = self.student.last_activity
        self.assertGreater(t2, past_time)

        # 5. Session 1 makes a request immediately after Session 2's update:
        # Must be throttled because Session 2 recently updated the user's persistent last_activity
        client1.get(reverse("dashboard"))
        self.student.refresh_from_db()
        self.assertEqual(self.student.last_activity, t2)

    def test_timezone_aware_comparison_and_naive_fallback(self):
        from datetime import datetime

        self.client.force_login(self.student)

        # Ensure normal update produces timezone-aware timestamp
        self.client.get(reverse("dashboard"))
        self.student.refresh_from_db()
        self.assertTrue(timezone.is_aware(self.student.last_activity))

        # Set a naive timestamp 90 seconds in the past
        import warnings
        naive_past = datetime.now() - timedelta(seconds=90)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            User.objects.filter(id=self.student.id).update(last_activity=naive_past)

        # Middleware should handle naive timestamp without TypeError and update
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)
        self.student.refresh_from_db()
        self.assertTrue(timezone.is_aware(self.student.last_activity))
        self.assertAlmostEqual(
            self.student.last_activity.timestamp(),
            timezone.now().timestamp(),
            delta=5,
        )

    def test_middleware_does_not_interfere_with_login_logout_and_password_flows(self):
        # 1. Login flow
        login_res = self.client.post(
            reverse("login"),
            {"username": "activity_student@pucit.edu.pk", "password": "Password123!"},
        )
        self.assertEqual(login_res.status_code, 302)

        # 2. Required password change flow
        self.student.must_change_password = True
        self.student.save(update_fields=["must_change_password"])

        dash_res = self.client.get(reverse("dashboard"))
        # Should be redirected to password_change by RequirePasswordChangeMiddleware
        self.assertEqual(dash_res.status_code, 302)
        self.assertRedirects(dash_res, reverse("password_change"))

        # Student's activity was still recorded by UserActivityMiddleware
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.last_activity)

        # 3. Password change submission
        change_res = self.client.post(
            reverse("password_change"),
            {
                "old_password": "Password123!",
                "new_password1": "NewStrongPass456!",
                "new_password2": "NewStrongPass456!",
            },
        )
        self.assertEqual(change_res.status_code, 302)
        self.student.refresh_from_db()
        self.assertFalse(self.student.must_change_password)

        # 4. Logout flow
        logout_res = self.client.post(reverse("logout"))
        self.assertIn(logout_res.status_code, [200, 302])


class ActiveUsersViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="cr_active@pucit.edu.pk",
            password="AdminPassword123!",
            role=User.Role.ADMIN,
            first_name="Hassan",
            last_name="Ali",
            must_change_password=False,
        )
        self.student_active_now = User.objects.create_user(
            email="stud_now@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A801",
            first_name="Zainab",
            last_name="Bibi",
            must_change_password=False,
        )
        self.student_seen_earlier = User.objects.create_user(
            email="stud_past@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A802",
            first_name="Bilal",
            last_name="Ahmed",
            must_change_password=False,
        )
        self.student_never_active = User.objects.create_user(
            email="stud_never@pucit.edu.pk",
            password="Password123!",
            role=User.Role.STUDENT,
            student_id="BITF24A803",
            first_name="Sana",
            last_name="Tariq",
            must_change_password=False,
        )

        now = timezone.now()
        # Active now: activity 2 minutes ago
        User.objects.filter(id=self.student_active_now.id).update(
            last_activity=now - timedelta(minutes=2)
        )
        # Seen earlier: activity 25 minutes ago
        User.objects.filter(id=self.student_seen_earlier.id).update(
            last_activity=now - timedelta(minutes=25)
        )
        # Never active: last_activity remains None

        self.url = reverse("active_users")

    def test_admin_can_access_active_users_page(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Currently Active Users")
        self.assertContains(res, "Active Now (&lt; 5 mins)")
        self.assertContains(res, "BITF24A801")
        self.assertContains(res, "BITF24A802")
        self.assertContains(res, "BITF24A803")

    def test_student_cannot_access_active_users_page(self):
        self.client.force_login(self.student_active_now)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

    def test_anonymous_user_redirected_to_login(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

    def test_active_now_classification(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)

        users = res.context["users"]
        now_entry = next(u for u in users if u["user"].id == self.student_active_now.id)
        self.assertTrue(now_entry["is_active_now"])

        earlier_entry = next(u for u in users if u["user"].id == self.student_seen_earlier.id)
        self.assertFalse(earlier_entry["is_active_now"])

        never_entry = next(u for u in users if u["user"].id == self.student_never_active.id)
        self.assertFalse(never_entry["is_active_now"])

    def test_disclaimer_rendered(self):
        self.client.force_login(self.admin)
        res = self.client.get(self.url)
        self.assertContains(res, "About Activity Tracking")
        self.assertContains(res, "estimate based on recent authenticated requests")

    def test_role_filtering_and_search(self):
        self.client.force_login(self.admin)

        # Filter by role=student
        res_students = self.client.get(f"{self.url}?role=student")
        self.assertEqual(res_students.status_code, 200)
        self.assertTrue(all(u["role"] == User.Role.STUDENT for u in res_students.context["users"]))

        # Search by student_id
        res_search = self.client.get(f"{self.url}?q=BITF24A801")
        self.assertEqual(res_search.status_code, 200)
        self.assertEqual(len(res_search.context["users"]), 1)
        self.assertEqual(res_search.context["users"][0]["student_id"], "BITF24A801")

    def test_dashboard_links_to_active_users_for_admin(self):
        self.client.force_login(self.admin)
        res = self.client.get(reverse("dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.url)
