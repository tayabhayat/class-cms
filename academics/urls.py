from django.urls import path
from .views import (
    dashboard,
    course_detail,
    mark_attendance,
    import_attendance,
    import_enrollments,
    my_attendance,
    manage_courses,
    create_course,
    edit_course,
    toggle_course_status,
    announcement_list,
    create_announcement,
    edit_announcement,
    delete_announcement,
    task_list,
    create_task,
    edit_task,
    task_detail,
    task_submission_report,
    download_submission,
    download_task_zip,
    poll_list,
    create_poll,
    poll_detail,
    close_poll,
)

urlpatterns = [
    path("dashboard/", dashboard, name="dashboard"),
    path("courses/", manage_courses, name="manage_courses"),
    path("courses/create/", create_course, name="create_course"),
    path("courses/<int:course_id>/", course_detail, name="course_detail"),
    path(
        "courses/<int:course_id>/edit/",
        edit_course,
        name="edit_course",
    ),
    path(
        "courses/<int:course_id>/toggle-status/",
        toggle_course_status,
        name="toggle_course_status",
    ),
    path(
        "courses/<int:course_id>/my-attendance/",
        my_attendance,
        name="my_attendance",
    ),
    path(
        "courses/<int:course_id>/attendance/",
        mark_attendance,
        name="mark_attendance",
    ),
    path(
        "attendance/import/",
        import_attendance,
        name="import_attendance",
    ),
    path(
        "enrollments/import/",
        import_enrollments,
        name="import_enrollments",
    ),
    path(
        "announcements/",
        announcement_list,
        name="announcement_list",
    ),
    path(
        "announcements/create/",
        create_announcement,
        name="create_announcement",
    ),
    path(
        "announcements/<int:announcement_id>/edit/",
        edit_announcement,
        name="edit_announcement",
    ),
    path(
        "announcements/<int:announcement_id>/delete/",
        delete_announcement,
        name="delete_announcement",
    ),
    # Tasks & Submissions
    path("tasks/", task_list, name="task_list"),
    path("tasks/create/", create_task, name="create_task"),
    path("tasks/<int:task_id>/", task_detail, name="task_detail"),
    path("tasks/<int:task_id>/edit/", edit_task, name="edit_task"),
    path(
        "tasks/<int:task_id>/submissions/",
        task_submission_report,
        name="task_submission_report",
    ),
    path(
        "tasks/<int:task_id>/download-zip/",
        download_task_zip,
        name="download_task_zip",
    ),
    path(
        "submissions/<int:submission_id>/download/",
        download_submission,
        name="download_submission",
    ),
    # Class Polls
    path("polls/", poll_list, name="poll_list"),
    path("polls/create/", create_poll, name="create_poll"),
    path("polls/<int:poll_id>/", poll_detail, name="poll_detail"),
    path("polls/<int:poll_id>/close/", close_poll, name="close_poll"),
]

