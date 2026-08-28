"""
اختبار وظيفي حقيقي لـ staff:order_list بعد التحويل لاستخدام
merge_orders_with_returns_for_staff (invoices/models.py) بدل التحميل الكامل
القديم لكل الطلبات والمرتجعات في الذاكرة.

الهدف: التأكد فعليًا إن:
1. عدد الاستعلامات ثابت (bounded) ومش بيكبر مع عدد الطلبات/المرتجعات
   الإجمالي في النظام (مش بس عميل واحد - هنا كل العملاء).
2. فلتر status بيشتغل صح (وبيخفي المرتجعات زي السلوك القديم بالظبط).
3. الترتيب والمحتوى صحيحين (orders + returns مع بعض، بيانات كل عميل).
4. حالات الصفحة الحدية (?page=abc, ?page=9999) مبترجعش 500.
5. نسخة العميل الواحد (merge_orders_with_returns) لسه شغالة زي الأول بعد
   إعادة الهيكلة المشتركة (_merge_and_paginate_order_return_rows).
"""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from invoices.models import Invoice, InvoiceReversal, merge_orders_with_returns
from orders.models import Order

User = get_user_model()


def _make_client_user(username):
    return User.objects.create_user(
        username=username,
        email=f'{username}@example.com',
        password='TestPass123!',
        role=User.Role.CLIENT,
        status=User.Status.ACTIVE,
    )


def _make_admin_user(username='staff_admin_test'):
    return User.objects.create_user(
        username=username,
        email=f'{username}@example.com',
        password='TestPass123!',
        role=User.Role.ADMIN,
        status=User.Status.ACTIVE,
    )


def _make_order(client, days_ago=0, status=None):
    order = Order.objects.create(client=client)
    updates = {'created_at': timezone.now() - timezone.timedelta(days=days_ago)}
    if status:
        updates['status'] = status
    Order.objects.filter(pk=order.pk).update(**updates)
    order.refresh_from_db()
    return order


def _make_reversal_for_order(order, days_ago=0):
    if not hasattr(order, 'invoice'):
        Invoice.objects.create(
            invoice_number=f'INV-ST-{order.pk:06d}',
            order=order,
            client_name=order.client.get_full_name() or order.client.username,
            total=100,
            is_draft=False,
        )
    invoice = order.invoice
    reversal = InvoiceReversal.objects.create(
        invoice=invoice, stage=InvoiceReversal.Stage.POST_DELIVERY, amount=10,
    )
    if days_ago:
        InvoiceReversal.objects.filter(pk=reversal.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=days_ago)
        )
        reversal.refresh_from_db()
    return reversal


class StaffOrderListViewTests(TestCase):
    """اختبار staff:order_list الفعلي عبر test client - لكل العملاء مع بعض."""

    def setUp(self):
        self.admin = _make_admin_user()
        self.client.login(username=self.admin.username, password='TestPass123!')

    def test_returns_200_when_admin_logged_in(self):
        response = self.client.get(reverse('staff:order_list'))
        self.assertEqual(response.status_code, 200)

    def test_query_count_bounded_across_dataset_sizes_multi_client(self):
        """
        العدد الفعلي لاستعلامات staff:order_list لازم يكون ثابت (مش بيكبر
        خطيًا) بغض النظر عن عدد العملاء أو الطلبات الإجمالي في النظام كله -
        ده بالظبط الفرق عن نسخة العميل الواحد (هنا مفيش فلتر client=).
        """
        small_clients = [_make_client_user(f'staff_small_c{i}') for i in range(3)]
        for c in small_clients:
            for i in range(5):
                order = _make_order(c, days_ago=i)
                if i < 2:
                    _make_reversal_for_order(order, days_ago=i)

        with CaptureQueriesContext(connection) as small_ctx:
            response_small = self.client.get(reverse('staff:order_list'))
        self.assertEqual(response_small.status_code, 200)
        small_count = len(small_ctx.captured_queries)

        # نزود عدد العملاء والطلبات بشكل كبير
        large_clients = [_make_client_user(f'staff_large_c{i}') for i in range(30)]
        for c in large_clients:
            for i in range(10):
                order = _make_order(c, days_ago=i)
                if i < 3:
                    _make_reversal_for_order(order, days_ago=i)

        with CaptureQueriesContext(connection) as large_ctx:
            response_large = self.client.get(reverse('staff:order_list'))
        self.assertEqual(response_large.status_code, 200)
        large_count = len(large_ctx.captured_queries)

        print(f'\n[staff order_list] small dataset (3 clients, 15 orders, 6 returns): {small_count} queries')
        print(f'[staff order_list] large dataset (33 clients, 315 orders, 51 returns): {large_count} queries')

        self.assertEqual(
            small_count, large_count,
            f'عدد استعلامات staff:order_list اختلف بين صغير ({small_count}) '
            f'وكبير ({large_count}) - فيه N+1 أو تحميل غير محدود لسه موجود.'
        )

    def test_status_filter_hides_returns(self):
        """
        فلتر status لازم يخفي صفوف المرتجعات تمامًا (نفس سلوك الكود القديم:
        'بتظهر بس في تبويب الكل لأن حالات الطلب مش منطبقة على إشعار مرتجع').
        """
        client = _make_client_user('staff_status_filter_client')
        order_pending = _make_order(client, days_ago=1, status=Order.Status.PENDING)
        order_confirmed = _make_order(client, days_ago=2, status=Order.Status.CONFIRMED)
        _make_reversal_for_order(order_confirmed, days_ago=0)

        response_all = self.client.get(reverse('staff:order_list'))
        rows_all = list(response_all.context['rows'])
        kinds_all = {r['kind'] for r in rows_all}
        self.assertIn('return', kinds_all, 'المرتجع لازم يظهر في تبويب الكل.')

        response_filtered = self.client.get(reverse('staff:order_list'), {'status': 'PENDING'})
        rows_filtered = list(response_filtered.context['rows'])
        kinds_filtered = {r['kind'] for r in rows_filtered}
        self.assertNotIn('return', kinds_filtered, 'المرتجع ظهر رغم تفعيل فلتر status - المفروض يختفي.')
        order_ids_filtered = {r['obj'].pk for r in rows_filtered if r['kind'] == 'order'}
        self.assertEqual(order_ids_filtered, {order_pending.pk})

    def test_orders_from_multiple_clients_all_appear(self):
        """قائمة الستاف المفروض تشمل طلبات كل العملاء، مش عميل واحد بس."""
        client_a = _make_client_user('staff_multi_client_a')
        client_b = _make_client_user('staff_multi_client_b')
        order_a = _make_order(client_a, days_ago=1)
        order_b = _make_order(client_b, days_ago=2)

        response = self.client.get(reverse('staff:order_list'))
        rows = list(response.context['rows'])
        order_ids = {r['obj'].pk for r in rows if r['kind'] == 'order'}

        self.assertIn(order_a.pk, order_ids)
        self.assertIn(order_b.pk, order_ids)

    def test_pagination_non_numeric_page_does_not_500(self):
        client = _make_client_user('staff_pg_abc_client')
        for i in range(40):
            _make_order(client, days_ago=i)
        response = self.client.get(reverse('staff:order_list'), {'page': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].number, 1)

    def test_pagination_out_of_range_page_does_not_500(self):
        client = _make_client_user('staff_pg_9999_client')
        for i in range(40):
            _make_order(client, days_ago=i)
        response = self.client.get(reverse('staff:order_list'), {'page': 9999})
        self.assertEqual(response.status_code, 200)
        last_page_number = response.context['page_obj'].paginator.num_pages
        self.assertEqual(response.context['page_obj'].number, last_page_number)

    def test_tags_still_attached_to_order_rows(self):
        """التأكد إن row['obj'].tag_list لسه بيتحط صح بعد إعادة الهيكلة."""
        client = _make_client_user('staff_tags_client')
        _make_order(client, days_ago=0)
        response = self.client.get(reverse('staff:order_list'))
        rows = list(response.context['rows'])
        order_rows = [r for r in rows if r['kind'] == 'order']
        self.assertTrue(order_rows)
        for row in order_rows:
            self.assertTrue(hasattr(row['obj'], 'tag_list'))


class ClientSideMergeStillWorksAfterRefactorTests(TestCase):
    """
    التأكد إن merge_orders_with_returns (نسخة العميل الواحد) لسه شغالة
    بالظبط زي الأول بعد فصل المنطق المشترك في
    _merge_and_paginate_order_return_rows - regression check.
    """

    def test_client_scoped_merge_only_returns_own_orders(self):
        client_a = _make_client_user('regression_client_a')
        client_b = _make_client_user('regression_client_b')

        order_a = _make_order(client_a, days_ago=1)
        _make_reversal_for_order(order_a, days_ago=0)
        order_b = _make_order(client_b, days_ago=1)
        _make_reversal_for_order(order_b, days_ago=0)

        orders_qs = Order.objects.filter(client=client_a)
        page = merge_orders_with_returns(orders_qs, client_a, page=1, page_size=20)
        rows = list(page)

        order_ids = {r['obj'].pk for r in rows if r['kind'] == 'order'}
        return_ids = {r['obj'].pk for r in rows if r['kind'] == 'return'}

        self.assertEqual(order_ids, {order_a.pk})
        self.assertEqual(len(return_ids), 1)
        reversal_obj = [r['obj'] for r in rows if r['kind'] == 'return'][0]
        self.assertEqual(reversal_obj.invoice.order.client_id, client_a.pk)
