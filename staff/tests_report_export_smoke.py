from django.test import Client as HttpClient, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from orders.models import Order, OrderItem
from invoices.models import Invoice
from products.models import Category, Product, ProductUnit


def make_admin():
    return User.objects.create_user(
        username='admin1', email='admin1@example.com', password='testpass123',
        role=User.Role.ADMIN,
    )


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class ReportExportSmokeTestCase(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.http.force_login(make_admin())

    def test_sales_export_flow_ends_in_downloadable_file(self):
        start_response = self.http.get(reverse('staff:reports_sales') + '?export=excel')
        self.assertEqual(start_response.status_code, 302)
        self.assertEqual(start_response.url, reverse('staff:reports_export_processing'))

        processing_response = self.http.get(reverse('staff:reports_export_processing'), follow=False)
        self.assertEqual(processing_response.status_code, 302)
        self.assertIn('/reports/export/download/', processing_response.url)

        download_response = self.http.get(processing_response.url)
        self.assertEqual(download_response.status_code, 200)
        self.assertIn('spreadsheetml', download_response['Content-Type'])

        # التوكن يتحرق بعد أول تحميل
        second_download = self.http.get(processing_response.url)
        self.assertEqual(second_download.status_code, 404)

    def test_products_export_with_no_sales_still_downloads(self):
        start_response = self.http.get(reverse('staff:reports_products') + '?export=excel&sort=qty')
        self.assertEqual(start_response.status_code, 302)
        processing_response = self.http.get(reverse('staff:reports_export_processing'))
        self.assertEqual(processing_response.status_code, 302)
        download_response = self.http.get(processing_response.url)
        self.assertEqual(download_response.status_code, 200)

    def test_stagnant_export_with_custom_days(self):
        start_response = self.http.get(reverse('staff:reports_stagnant') + '?export=excel&days=90')
        self.assertEqual(start_response.status_code, 302)
        processing_response = self.http.get(reverse('staff:reports_export_processing'))
        self.assertEqual(processing_response.status_code, 302)
        download_response = self.http.get(processing_response.url)
        self.assertEqual(download_response.status_code, 200)

    def test_customers_profit_and_supply_suggestions_export(self):
        for url_name, extra in [
            ('staff:reports_customers', ''),
            ('staff:reports_profit', ''),
            ('staff:reports_supply_suggestions', ''),
        ]:
            with self.subTest(url_name=url_name):
                start_response = self.http.get(reverse(url_name) + '?export=excel' + extra)
                self.assertEqual(start_response.status_code, 302)
                processing_response = self.http.get(reverse('staff:reports_export_processing'))
                self.assertEqual(processing_response.status_code, 302)
                download_response = self.http.get(processing_response.url)
                self.assertEqual(download_response.status_code, 200)
                self.assertIn('spreadsheetml', download_response['Content-Type'])

    def test_pagination_notice_partial_shown_only_with_multiple_pages(self):
        from django.core.paginator import Paginator
        from django.template.loader import render_to_string

        single_page = Paginator(range(10), 50).get_page(1)
        html_single = render_to_string(
            'staff/reports/partials/_print_pagination_notice.html', {'page_obj': single_page},
        )
        self.assertNotIn('الطباعة بتشمل الصفحة الحالية بس', html_single)

        multi_page = Paginator(range(120), 50).get_page(1)
        html_multi = render_to_string(
            'staff/reports/partials/_print_pagination_notice.html', {'page_obj': multi_page},
        )
        self.assertIn('الطباعة بتشمل الصفحة الحالية بس', html_multi)
        self.assertIn('120', html_multi)

    def test_processing_without_started_export_redirects(self):
        response = self.http.get(reverse('staff:reports_export_processing'))
        self.assertRedirects(response, reverse('staff:reports_dashboard'))
