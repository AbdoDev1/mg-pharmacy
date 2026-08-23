"""
اختبارات export_products / export_products_selected بعد نقلهم لـ Celery
(راجع mg-pharmacy-tech-debt-audit.md، البند 2). زي ما موصّى في
mg-pharmacy-testing-strategy.md: CELERY_TASK_ALWAYS_EAGER=True عشان
build_products_export تتنفذ فعليًا جوه نفس الاختبار (مش تتأجل لـ worker
حقيقي)، فنقدر نتأكد من الحالة النهائية في الجلسة ومن محتوى الملف.
"""
from django.test import Client as HttpClient, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from products.models import Category, Product, ProductUnit


def make_admin():
    return User.objects.create_user(
        username='admin1', email='admin1@example.com', password='testpass123',
        role=User.Role.ADMIN,
    )


def make_product(name, code, category):
    product = Product.objects.create(name_ar=name, code=code, category=category)
    ProductUnit.objects.create(
        product=product, name='قطعة', size='S', qty_in_small=1, unit_price=10,
    )
    return product


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class ExportProductsFlowTestCase(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.product = make_product('دواء 1', 'P1', self.category)

    def test_requires_view_product_permission(self):
        """موظف مخزن من غير صلاحية products.view_product مايقدرش يبدأ التصدير."""
        staff = User.objects.create_user(
            username='wh1', email='wh1@example.com', password='testpass123',
            role=User.Role.WAREHOUSE, status=User.Status.ACTIVE,
        )
        self.http.force_login(staff)
        response = self.http.get(reverse('staff:export_products'))
        self.assertRedirects(response, reverse('staff:dashboard'))

    def test_export_all_products_flow_ends_in_downloadable_file(self):
        self.http.force_login(make_admin())

        start_response = self.http.get(reverse('staff:export_products'))
        self.assertEqual(start_response.status_code, 302)
        self.assertEqual(start_response.url, reverse('staff:export_products_processing'))

        # مع CELERY_TASK_ALWAYS_EAGER، الـ task خلصت خلال .delay() نفسها،
        # فالحالة في الجلسة المفروض تبقى 'done' فورًا.
        processing_response = self.http.get(reverse('staff:export_products_processing'), follow=False)
        self.assertEqual(processing_response.status_code, 302)
        self.assertIn('/products/export/download/', processing_response.url)

        download_response = self.http.get(processing_response.url)
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(
            download_response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.assertIn('mgpharmacy_products_export.xlsx', download_response['Content-Disposition'])

    def test_download_token_is_single_use(self):
        self.http.force_login(make_admin())
        self.http.get(reverse('staff:export_products'))
        processing_response = self.http.get(reverse('staff:export_products_processing'))
        download_url = processing_response.url

        first = self.http.get(download_url)
        second = self.http.get(download_url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 404)

    def test_processing_page_without_started_export_redirects_to_product_list(self):
        self.http.force_login(make_admin())
        response = self.http.get(reverse('staff:export_products_processing'))
        self.assertRedirects(response, reverse('staff:product_list'))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class ExportProductsSelectedTestCase(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.product1 = make_product('دواء 1', 'P1', self.category)
        self.product2 = make_product('دواء 2', 'P2', self.category)

    def test_no_ids_selected_shows_warning_and_redirects(self):
        self.http.force_login(make_admin())
        response = self.http.post(reverse('staff:export_products_selected'), {})
        self.assertRedirects(response, reverse('staff:export_products_select'))

    def test_selected_ids_flow_ends_in_downloadable_file(self):
        self.http.force_login(make_admin())
        start_response = self.http.post(
            reverse('staff:export_products_selected'), {'product_ids': [self.product1.pk]},
        )
        self.assertEqual(start_response.status_code, 302)
        self.assertEqual(start_response.url, reverse('staff:export_products_processing'))

        processing_response = self.http.get(reverse('staff:export_products_processing'))
        download_response = self.http.get(processing_response.url)

        self.assertEqual(download_response.status_code, 200)
        self.assertIn('mgpharmacy_products_export_selected.xlsx', download_response['Content-Disposition'])

    def test_get_request_redirects_without_starting_export(self):
        self.http.force_login(make_admin())
        response = self.http.get(reverse('staff:export_products_selected'))
        self.assertRedirects(response, reverse('staff:export_products_select'))
