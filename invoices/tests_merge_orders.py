"""
اختبار وظيفي حقيقي لـ merge_orders_with_returns (invoices/models.py).

الهدف: التأكد فعليًا (مش بالقراءة) إن:
1. عدد الاستعلامات ثابت (bounded) ومش بيكبر مع عدد الطلبات/المرتجعات.
2. الترتيب صحيح وحتمي (deterministic) حتى مع تساوي created_at.
3. الـpagination شغال صح عبر order_list وdashboard الفعليين (مش استدعاء الدالة
   مباشرة بس)، بما في ذلك باراميترات صفحة غير صالحة (?page=abc, ?page=9999).
4. المحتوى (kind/type) صحيح لكل صف.
"""
from django.contrib.auth import get_user_model
from django.core.paginator import Page
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from invoices.models import Invoice, InvoiceReversal, merge_orders_with_returns
from orders.models import Order

User = get_user_model()


def _make_client_user(username='client_merge_test'):
    return User.objects.create_user(
        username=username,
        email=f'{username}@example.com',
        password='TestPass123!',
        role=User.Role.CLIENT,
        status=User.Status.ACTIVE,
    )


def _make_order(client, days_ago=0):
    order = Order.objects.create(client=client)
    if days_ago:
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=days_ago)
        )
        order.refresh_from_db()
    return order


def _make_reversal_for_order(order, seq, days_ago=0, actor=None):
    """
    بينشئ Invoice (لو مش موجودة أصلًا للطلب ده) وInvoiceReversal مرتبط بيها،
    من غير المرور بمسار confirm()/issue_for_order() الكامل (اللي محتاج أصناف
    منتجات حقيقية) — الهدف هنا اختبار الدمج/الترتيب/الـpagination بس، مش
    مسار الفوترة نفسه (ده مغطى في invoices/tests.py و tests_returns.py).
    """
    if not hasattr(order, 'invoice'):
        Invoice.objects.create(
            invoice_number=f'INV-TEST-{order.pk:06d}',
            order=order,
            client_name=order.client.get_full_name() or order.client.username,
            total=100,
            is_draft=False,
        )
    invoice = order.invoice
    reversal = InvoiceReversal.objects.create(
        invoice=invoice,
        stage=InvoiceReversal.Stage.POST_DELIVERY,
        amount=10,
        created_by=actor,
    )
    if days_ago:
        InvoiceReversal.objects.filter(pk=reversal.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=days_ago)
        )
        reversal.refresh_from_db()
    return reversal


class MergeOrdersWithReturnsFunctionTests(TestCase):
    """اختبار الدالة نفسها مباشرة: عدد الاستعلامات + الترتيب + المحتوى."""

    def test_query_count_is_bounded_not_linear(self):
        """
        العدد الفعلي للاستعلامات المستخدمة داخل الدالة نفسها لازم يكون ثابت
        (نفس العدد تقريبًا) بغض النظر عن حجم البيانات - مش بيكبر خطيًا.
        """
        small_client = _make_client_user('client_small_direct')
        for i in range(10):
            _make_order(small_client, days_ago=i)
        for i in range(5):
            order = Order.objects.filter(client=small_client).order_by('created_at')[i]
            _make_reversal_for_order(order, i, days_ago=i)

        large_client = _make_client_user('client_large_direct')
        for i in range(100):
            _make_order(large_client, days_ago=i)
        for i in range(30):
            order = Order.objects.filter(client=large_client).order_by('created_at')[i]
            _make_reversal_for_order(order, i, days_ago=i)

        with CaptureQueriesContext(connection) as small_ctx:
            small_orders_qs = Order.objects.filter(client=small_client)
            small_page = merge_orders_with_returns(small_orders_qs, small_client, page=1, page_size=20)
            list(small_page)  # نتأكد إن كل الصفحة اتحمّلت فعليًا (lazy evaluation)
        small_query_count = len(small_ctx.captured_queries)

        with CaptureQueriesContext(connection) as large_ctx:
            large_orders_qs = Order.objects.filter(client=large_client)
            large_page = merge_orders_with_returns(large_orders_qs, large_client, page=1, page_size=20)
            list(large_page)
        large_query_count = len(large_ctx.captured_queries)

        print(f'\n[query_count] small dataset (10 orders + 5 returns): {small_query_count} queries')
        print(f'[query_count] large dataset (100 orders + 30 returns): {large_query_count} queries')

        # لازم يكون نفس العدد بالظبط (الدالة بتعمل عدد ثابت من الاستعلامات:
        # union count + union select + orders select + reversals select).
        self.assertEqual(
            small_query_count, large_query_count,
            f'عدد الاستعلامات اختلف بين حجم صغير ({small_query_count}) وحجم كبير '
            f'({large_query_count}) — ده معناه فيه N+1 لسه موجود.'
        )
        # سقف معقول (4-6 استعلامات متوقعة): count للـpaginator + select للـunion
        # + select للـorders + select للـreversals. أي رقم أعلى بكتير يستاهل تحقيق.
        self.assertLessEqual(
            small_query_count, 6,
            f'عدد الاستعلامات ({small_query_count}) أعلى من المتوقع لعملية bounded.'
        )

    def test_ordering_and_determinism(self):
        client = _make_client_user('client_ordering')
        now = timezone.now()

        # طلب ومرتجع بنفس created_at بالظبط - لازم الترتيب يفضل حتمي
        order_a = Order.objects.create(client=client)
        Order.objects.filter(pk=order_a.pk).update(created_at=now)
        order_a.refresh_from_db()

        order_b = Order.objects.create(client=client)
        Order.objects.filter(pk=order_b.pk).update(created_at=now - timezone.timedelta(hours=1))
        order_b.refresh_from_db()

        reversal_same_time = _make_reversal_for_order(order_a, 0)
        InvoiceReversal.objects.filter(pk=reversal_same_time.pk).update(created_at=now)
        reversal_same_time.refresh_from_db()

        orders_qs = Order.objects.filter(client=client)
        page = merge_orders_with_returns(orders_qs, client, page=1, page_size=20)
        rows = list(page)

        # المفروض 3 صفوف: order_a + reversal_same_time (نفس created_at) + order_b (أقدم)
        self.assertEqual(len(rows), 3)

        created_at_values = [row['created_at'] for row in rows]
        self.assertEqual(
            created_at_values, sorted(created_at_values, reverse=True),
            'الصفوف مش مرتبة تنازليًا حسب created_at.'
        )

        # order_b (الأقدم) لازم يكون آخر صف
        self.assertEqual(rows[-1]['kind'], 'order')
        self.assertEqual(rows[-1]['obj'].pk, order_b.pk)

        # الاتنين اللي بنفس created_at (order_a + reversal) لازم يكونوا أول صفين
        # بترتيب حتمي (rank: order=0 قبل return=1 عند تساوي created_at، حسب
        # tiebreaker source_rank في الدالة نفسها).
        first_two_kinds = {rows[0]['kind'], rows[1]['kind']}
        self.assertEqual(first_two_kinds, {'order', 'return'})

        # التأكد إن الترتيب حتمي: نفس الاستدعاء تاني لازم يرجّع نفس الترتيب بالظبط
        page_again = merge_orders_with_returns(orders_qs, client, page=1, page_size=20)
        rows_again = list(page_again)
        self.assertEqual(
            [(r['kind'], r['obj'].pk) for r in rows],
            [(r['kind'], r['obj'].pk) for r in rows_again],
            'الترتيب مش حتمي - نفس الاستدعاء رجّع ترتيب مختلف.'
        )

    def test_content_integrity(self):
        client = _make_client_user('client_content')
        orders = [_make_order(client, days_ago=i) for i in range(10)]
        reversals = []
        for i in range(5):
            reversals.append(_make_reversal_for_order(orders[i], i, days_ago=i))

        orders_qs = Order.objects.filter(client=client)
        page = merge_orders_with_returns(orders_qs, client, page=1, page_size=20)
        rows = list(page)

        self.assertIsInstance(page, Page)

        order_rows = [r for r in rows if r['kind'] == 'order']
        return_rows = [r for r in rows if r['kind'] == 'return']

        self.assertEqual(len(order_rows), 10)
        self.assertEqual(len(return_rows), 5)

        self.assertEqual(
            {r['obj'].pk for r in order_rows},
            {o.pk for o in orders},
        )
        self.assertEqual(
            {r['obj'].pk for r in return_rows},
            {rv.pk for rv in reversals},
        )

        for row in order_rows:
            self.assertIsInstance(row['obj'], Order)
        for row in return_rows:
            self.assertIsInstance(row['obj'], InvoiceReversal)


class OrderListViewTests(TestCase):
    """اختبار orders:order_list الفعلي عبر test client - مش استدعاء الدالة مباشرة."""

    def setUp(self):
        self.client_user = _make_client_user('client_order_list_view')
        self.client.login(username='client_order_list_view', password='TestPass123!')

    def test_returns_200_when_logged_in(self):
        response = self.client.get(reverse('orders:order_list'))
        self.assertEqual(response.status_code, 200)

    def test_query_count_bounded_across_dataset_sizes(self):
        """
        نقيس عدد استعلامات order_list الفعلية (view كامل، مش الدالة لوحدها)
        بين مجموعة بيانات صغيرة وكبيرة، لنفس العميل - لازم يكون نفس العدد
        تقريبًا (فرق صفري متوقع، لأن الـpagination بيقتصر النتيجة).
        """
        for i in range(10):
            _make_order(self.client_user, days_ago=i)
        for i in range(5):
            order = Order.objects.filter(client=self.client_user).order_by('created_at')[i]
            _make_reversal_for_order(order, i, days_ago=i)

        with CaptureQueriesContext(connection) as small_ctx:
            response_small = self.client.get(reverse('orders:order_list'))
        self.assertEqual(response_small.status_code, 200)
        small_count = len(small_ctx.captured_queries)

        # نضيف كمية كبيرة من الطلبات/المرتجعات لنفس العميل
        for i in range(10, 110):
            _make_order(self.client_user, days_ago=i)
        for i in range(5, 35):
            order = Order.objects.filter(client=self.client_user).order_by('created_at')[i]
            _make_reversal_for_order(order, i, days_ago=i)

        with CaptureQueriesContext(connection) as large_ctx:
            response_large = self.client.get(reverse('orders:order_list'))
        self.assertEqual(response_large.status_code, 200)
        large_count = len(large_ctx.captured_queries)

        print(f'\n[order_list view] small dataset queries: {small_count}')
        print(f'[order_list view] large dataset queries: {large_count}')

        self.assertEqual(
            small_count, large_count,
            f'عدد استعلامات order_list اختلف بين صغير ({small_count}) وكبير '
            f'({large_count}).'
        )

    def test_pagination_non_numeric_page_does_not_500(self):
        for i in range(25):
            _make_order(self.client_user, days_ago=i)
        response = self.client.get(reverse('orders:order_list'), {'page': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].number, 1)

    def test_pagination_out_of_range_page_does_not_500(self):
        for i in range(25):
            _make_order(self.client_user, days_ago=i)
        response = self.client.get(reverse('orders:order_list'), {'page': 9999})
        self.assertEqual(response.status_code, 200)
        # get_page بيرجع آخر صفحة موجودة بدل ما يفشل
        last_page_number = response.context['page_obj'].paginator.num_pages
        self.assertEqual(response.context['page_obj'].number, last_page_number)

    def test_pagination_page_2_returns_different_rows_than_page_1(self):
        for i in range(25):
            _make_order(self.client_user, days_ago=i)
        response_p1 = self.client.get(reverse('orders:order_list'), {'page': 1})
        response_p2 = self.client.get(reverse('orders:order_list'), {'page': 2})
        rows_p1 = {row['obj'].pk for row in response_p1.context['rows']}
        rows_p2 = {row['obj'].pk for row in response_p2.context['rows']}
        self.assertTrue(rows_p1, 'الصفحة الأولى فاضية.')
        self.assertTrue(rows_p2, 'الصفحة التانية فاضية.')
        self.assertEqual(
            rows_p1 & rows_p2, set(),
            'نفس الصف ظهر في الصفحة الأولى والتانية - فيه مشكلة في الـpagination.'
        )


class DashboardViewTests(TestCase):
    """اختبار accounts:dashboard الفعلي - باراميتر ?orders_page مختلف عن ?page."""

    def setUp(self):
        self.client_user = _make_client_user('client_dashboard_view')
        self.client.login(username='client_dashboard_view', password='TestPass123!')

    def test_returns_200_when_logged_in(self):
        response = self.client.get(reverse('accounts:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_uses_orders_page_param_not_page(self):
        """
        dashboard المفروض يستخدم ?orders_page مش ?page (عشان مايتعارضش مع
        ?statement_page بتاع كشف الحساب). نتأكد إن تمرير orders_page فعليًا
        بيغيّر الصفحة المعروضة في تبويب الطلبات.
        """
        for i in range(15):
            _make_order(self.client_user, days_ago=i)

        response_p1 = self.client.get(reverse('accounts:dashboard'), {'orders_page': 1})
        response_p2 = self.client.get(reverse('accounts:dashboard'), {'orders_page': 2})

        self.assertEqual(response_p1.status_code, 200)
        self.assertEqual(response_p2.status_code, 200)

        rows_p1 = {row['obj'].pk for row in response_p1.context['orders_page_obj']}
        rows_p2 = {row['obj'].pk for row in response_p2.context['orders_page_obj']}

        self.assertTrue(rows_p1)
        self.assertTrue(rows_p2)
        self.assertEqual(
            rows_p1 & rows_p2, set(),
            'orders_page=1 وorders_page=2 رجّعوا نفس الصفوف - الباراميتر مش شغال صح.'
        )

    def test_page_param_does_not_affect_orders_tab(self):
        """
        ?page (لو اتبعت غلط بدل ?orders_page) مايفترضش يأثر على تبويب
        الطلبات في dashboard، لأن الدالة بتقرا request.GET.get('orders_page')
        تحديدًا مش request.GET.get('page').
        """
        for i in range(15):
            _make_order(self.client_user, days_ago=i)

        response_default = self.client.get(reverse('accounts:dashboard'))
        response_with_page = self.client.get(reverse('accounts:dashboard'), {'page': 2})

        rows_default = [row['obj'].pk for row in response_default.context['orders_page_obj']]
        rows_with_page = [row['obj'].pk for row in response_with_page.context['orders_page_obj']]

        self.assertEqual(
            rows_default, rows_with_page,
            '?page أثّر على تبويب الطلبات في dashboard - المفروض بس ?orders_page يأثر.'
        )

    def test_pagination_non_numeric_orders_page_does_not_500(self):
        for i in range(15):
            _make_order(self.client_user, days_ago=i)
        response = self.client.get(reverse('accounts:dashboard'), {'orders_page': 'abc'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['orders_page_obj'].number, 1)

    def test_pagination_out_of_range_orders_page_does_not_500(self):
        for i in range(15):
            _make_order(self.client_user, days_ago=i)
        response = self.client.get(reverse('accounts:dashboard'), {'orders_page': 9999})
        self.assertEqual(response.status_code, 200)
        last_page_number = response.context['orders_page_obj'].paginator.num_pages
        self.assertEqual(response.context['orders_page_obj'].number, last_page_number)
