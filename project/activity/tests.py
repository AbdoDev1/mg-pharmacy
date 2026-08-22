"""
اختبارات activity — تطبيق سجل التدقيق (audit trail) العام. كان بدون أي
اختبار خالص (راجع mg-pharmacy-tech-debt-audit.md، البند 1)، مع إن أي
خطأ صامت هنا (سجل ناقص، فلترة غلط، صلاحيات) ممكن يفضل مخفي لحد ما يتلغى
في نزاع أو مراجعة فعلية. البداية دي أساسية (تسجيل، فلترة، صلاحيات) —
مش تغطية كاملة من أول مرة، زي ما موصّى في mg-pharmacy-testing-strategy.md.
"""
from django.contrib.contenttypes.models import ContentType
from django.test import Client as HttpClient, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from products.models import Category
from .models import ActivityLog
from .services import (
    delete_activity_logs_for,
    delete_old_activity_logs,
    diff_summary,
    log_activity,
    log_created,
    log_note,
)


def make_staff(role=User.Role.WAREHOUSE, username=None):
    username = username or f'staff_{role.lower()}'
    return User.objects.create_user(
        username=username, email=f'{username}@example.com',
        password='testpass123', role=role, status=User.Status.ACTIVE,
    )


def make_client_user(username='client1'):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com',
        password='testpass123', role=User.Role.CLIENT,
    )


class LogActivityServiceTestCase(TestCase):
    """log_activity / log_created / log_note — تسجيل الحدث نفسه."""

    def setUp(self):
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.user = make_staff()

    def test_log_activity_records_content_type_object_id_and_actor(self):
        log = log_activity(
            self.category, ActivityLog.Event.UPDATED, user=self.user,
            changes_summary='الاسم: أدوية → أدوية عامة',
        )
        self.assertEqual(log.content_type, ContentType.objects.get_for_model(Category))
        self.assertEqual(log.object_id, self.category.pk)
        self.assertEqual(log.created_by, self.user)
        self.assertEqual(log.event, ActivityLog.Event.UPDATED)
        self.assertEqual(log.changes_summary, 'الاسم: أدوية → أدوية عامة')

    def test_log_activity_without_user_records_null_actor(self):
        """تسجيل تلقائي من الكود (مش من موظف مباشرة) — created_by ممكن يبقى فاضي."""
        log = log_activity(self.category, ActivityLog.Event.CREATED)
        self.assertIsNone(log.created_by)

    def test_log_created_uses_created_event(self):
        log = log_created(self.category, user=self.user)
        self.assertEqual(log.event, ActivityLog.Event.CREATED)

    def test_log_note_stores_note_text_and_note_event(self):
        log = log_note(self.category, 'ملاحظة داخلية', user=self.user)
        self.assertEqual(log.event, ActivityLog.Event.NOTE)
        self.assertEqual(log.note, 'ملاحظة داخلية')

    def test_content_object_resolves_to_original_instance(self):
        log = log_activity(self.category, ActivityLog.Event.CREATED, user=self.user)
        self.assertEqual(log.content_object, self.category)


class DiffSummaryTestCase(TestCase):
    """diff_summary — الوصف التلقائي المختصر لما اتغيّر فعليًا."""

    def setUp(self):
        self.category = Category.objects.create(name='أدوية', slug='meds')

    def test_no_change_returns_empty_string(self):
        old_values = {'name': self.category.name}
        summary = diff_summary(old_values, self.category, ['name'])
        self.assertEqual(summary, '')

    def test_changed_field_appears_in_summary(self):
        old_values = {'name': 'أدوية'}
        self.category.name = 'أدوية عامة'
        summary = diff_summary(old_values, self.category, ['name'])
        self.assertIn('أدوية', summary)
        self.assertIn('أدوية عامة', summary)
        self.assertIn('→', summary)

    def test_only_changed_fields_included_not_unchanged_ones(self):
        old_values = {'name': 'أدوية', 'slug': 'meds'}
        self.category.name = 'أدوية عامة'
        # slug متغيرش
        summary = diff_summary(old_values, self.category, ['name', 'slug'])
        self.assertIn('أدوية عامة', summary)
        self.assertNotIn('meds', summary)


class DeleteActivityLogsTestCase(TestCase):
    """delete_activity_logs_for — تنظيف السجلات اليتيمة قبل حذف الكيان الأصلي."""

    def test_deletes_only_logs_for_the_given_instance(self):
        category1 = Category.objects.create(name='قسم 1', slug='c1')
        category2 = Category.objects.create(name='قسم 2', slug='c2')
        log_activity(category1, ActivityLog.Event.CREATED)
        log_activity(category1, ActivityLog.Event.UPDATED)
        log_activity(category2, ActivityLog.Event.CREATED)

        delete_activity_logs_for(category1)

        content_type = ContentType.objects.get_for_model(Category)
        self.assertEqual(
            ActivityLog.objects.filter(content_type=content_type, object_id=category1.pk).count(), 0,
        )
        self.assertEqual(
            ActivityLog.objects.filter(content_type=content_type, object_id=category2.pk).count(), 1,
        )


class DeleteOldActivityLogsTestCase(TestCase):
    """delete_old_activity_logs — أمر trim_activity_logs (retention)."""

    def test_deletes_logs_older_than_cutoff_keeps_recent_ones(self):
        category = Category.objects.create(name='أدوية', slug='meds')
        old_log = log_activity(category, ActivityLog.Event.CREATED)
        recent_log = log_activity(category, ActivityLog.Event.UPDATED)

        ActivityLog.objects.filter(pk=old_log.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=100),
        )

        deleted_count = delete_old_activity_logs(days=90)

        self.assertEqual(deleted_count, 1)
        self.assertFalse(ActivityLog.objects.filter(pk=old_log.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(pk=recent_log.pk).exists())


class ExcludePricingDetailsQuerySetTestCase(TestCase):
    """
    exclude_pricing_details — بيانات تسعير/خصومات حساسة لازم متظهرش في
    سجل الأنشطة العام خالص (راجع تعليق الموديل نفسه).
    """

    def setUp(self):
        self.category = Category.objects.create(name='أدوية', slug='meds')

    def test_excludes_logs_mentioning_price(self):
        log_activity(self.category, ActivityLog.Event.UPDATED, changes_summary='السعر: 10 → 12')
        self.assertEqual(ActivityLog.objects.exclude_pricing_details().count(), 0)

    def test_excludes_logs_mentioning_discount(self):
        log_activity(self.category, ActivityLog.Event.UPDATED, changes_summary='الخصم: 5% → 10%')
        self.assertEqual(ActivityLog.objects.exclude_pricing_details().count(), 0)

    def test_keeps_logs_with_no_pricing_mention(self):
        log_activity(self.category, ActivityLog.Event.UPDATED, changes_summary='الاسم: أ → ب')
        self.assertEqual(ActivityLog.objects.exclude_pricing_details().count(), 1)


class AddNoteViewTestCase(TestCase):
    """add_note — نقطة النهاية العامة لإضافة ملاحظة (Chatter) على أي كيان."""

    def setUp(self):
        self.http = HttpClient()
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.url = reverse(
            'activity:add_note',
            args=['products', 'category', self.category.pk],
        )

    def test_requires_login(self):
        response = self.http.post(self.url, {'note': 'ملاحظة'})
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(ActivityLog.objects.count(), 0)

    def test_client_role_cannot_add_note(self):
        """العميل مش موظف — الشرط في الـ view: أدمن أو مخزن بس."""
        self.http.force_login(make_client_user())
        self.http.post(self.url, {'note': 'ملاحظة'})
        self.assertEqual(ActivityLog.objects.count(), 0)

    def test_staff_can_add_note(self):
        self.http.force_login(make_staff())
        self.http.post(self.url, {'note': 'ملاحظة داخلية'})
        self.assertEqual(ActivityLog.objects.count(), 1)
        log = ActivityLog.objects.first()
        self.assertEqual(log.event, ActivityLog.Event.NOTE)
        self.assertEqual(log.note, 'ملاحظة داخلية')

    def test_empty_note_is_not_recorded(self):
        self.http.force_login(make_staff())
        self.http.post(self.url, {'note': '   '})
        self.assertEqual(ActivityLog.objects.count(), 0)


class ActivityListViewTestCase(TestCase):
    """activity_list — عرض/بحث/فلترة (staff:activity_list)."""

    def setUp(self):
        self.http = HttpClient()
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.url = reverse('staff:activity_list')

    def test_requires_permission(self):
        """موظف مخزن من غير صلاحية activity.view_activitylog مايشوفش الصفحة."""
        self.http.force_login(make_staff())
        response = self.http.get(self.url)
        self.assertRedirects(response, reverse('staff:dashboard'))

    def test_admin_can_view_activity_list(self):
        log_activity(self.category, ActivityLog.Event.CREATED)
        self.http.force_login(make_staff(role=User.Role.ADMIN))
        response = self.http.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_search_filters_by_note_content(self):
        log_note(self.category, 'ملاحظة عن التوصيل', user=make_staff())
        log_note(self.category, 'حاجة تانية خالص', user=make_staff(username='staff2'))
        self.http.force_login(make_staff(role=User.Role.ADMIN, username='admin1'))

        response = self.http.get(self.url, {'q': 'التوصيل'})

        self.assertEqual(response.context['page_obj'].paginator.count, 1)

    def test_event_filter_returns_only_matching_event(self):
        log_created(self.category)
        log_note(self.category, 'ملاحظة', user=make_staff())
        self.http.force_login(make_staff(role=User.Role.ADMIN, username='admin1'))

        response = self.http.get(self.url, {'event': ActivityLog.Event.NOTE})

        results = list(response.context['page_obj'].object_list)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].event, ActivityLog.Event.NOTE)

    def test_pricing_related_logs_are_excluded_from_list(self):
        log_activity(self.category, ActivityLog.Event.UPDATED, changes_summary='السعر: 10 → 12')
        self.http.force_login(make_staff(role=User.Role.ADMIN, username='admin1'))

        response = self.http.get(self.url)

        self.assertEqual(response.context['page_obj'].paginator.count, 0)
