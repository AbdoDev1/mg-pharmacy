"""
اختبارات backup_run_now بعد نقلها لـ Celery (راجع mg-pharmacy-tech-debt-audit.md،
البند 6). بنعمل mock لـ run_backup_task.delay بدل ما نشغّل perform_backup()
الحقيقية (pg_dump فعلي) — الهدف هنا اختبار طبقة التنسيق (orchestration:
الصلاحية، طريقة الطلب، إن الـ task اتنادت) مش منطق النسخ نفسه (ده له
اختباراته الخاصة المقترحة في mg-pharmacy-testing-strategy.md لـ
staff/services/backup.py، بعيد عن نطاق التغيير ده).
"""
from unittest.mock import patch

from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from accounts.models import User


def make_admin():
    return User.objects.create_user(
        username='admin1', email='admin1@example.com', password='testpass123',
        role=User.Role.ADMIN,
    )


class BackupRunNowTestCase(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.url = reverse('staff:backup_run_now')

    def test_requires_manage_backup_permission(self):
        staff = User.objects.create_user(
            username='wh1', email='wh1@example.com', password='testpass123',
            role=User.Role.WAREHOUSE, status=User.Status.ACTIVE,
        )
        self.http.force_login(staff)
        with patch('staff.tasks.run_backup_task.delay') as mock_delay:
            response = self.http.post(self.url)
        mock_delay.assert_not_called()
        self.assertRedirects(response, reverse('staff:dashboard'))

    def test_get_request_does_not_trigger_backup(self):
        self.http.force_login(make_admin())
        with patch('staff.tasks.run_backup_task.delay') as mock_delay:
            response = self.http.get(self.url)
        mock_delay.assert_not_called()
        self.assertRedirects(response, reverse('staff:backup_manual'))

    def test_post_triggers_task_and_returns_immediately(self):
        """الطلب نفسه لازم يرجع فورًا (delay بس، مش استدعاء perform_backup مباشرة)."""
        self.http.force_login(make_admin())
        with patch('staff.tasks.run_backup_task.delay') as mock_delay:
            response = self.http.post(self.url)
        mock_delay.assert_called_once_with()
        self.assertRedirects(response, reverse('staff:backup_manual'))

    def test_success_message_mentions_background(self):
        self.http.force_login(make_admin())
        with patch('staff.tasks.run_backup_task.delay'):
            response = self.http.post(self.url, follow=True)
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(any('الخلفية' in m for m in messages))
