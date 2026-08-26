from decimal import Decimal

from django.test import Client as HttpClient, TestCase
from django.urls import reverse

from accounts.models import User
from accounts.models import ClientProfile
from invoices.models import Invoice, InvoiceItem, InvoiceReversal, InvoiceReversalItem
from orders.models import Order, OrderItem
from products.models import Category, Product, ProductUnit


def make_admin():
    return User.objects.create_user(
        username='admin1', email='admin1@example.com', password='testpass123',
        role=User.Role.ADMIN,
    )


def make_client():
    user = User.objects.create_user(
        username='client1', email='client1@example.com', password='testpass123',
        role=User.Role.CLIENT, status=User.Status.ACTIVE,
    )
    return user


class PrintSmokeTestCase(TestCase):
    def setUp(self):
        self.http = HttpClient()
        self.admin = make_admin()
        self.client_user = make_client()
        self.category = Category.objects.create(name='أدوية', slug='meds')
        self.product = Product.objects.create(name_ar='دواء تجريبي', code='P1', category=self.category)
        self.unit = ProductUnit.objects.create(
            product=self.product, name='قطعة', size='S', qty_in_small=1, unit_price=50,
        )
        self.order = Order.objects.create(client=self.client_user, status=Order.Status.DELIVERED)
        OrderItem.objects.create(
            order=self.order, product_unit=self.unit, quantity=3,
            unit_price=50, public_price=60,
        )
        self.invoice = Invoice.objects.create(
            order=self.order, invoice_number='INV-TEST-0001', total=Decimal('150.00'),
            client_name='عميل تجريبي', issued_by=self.admin,
        )
        InvoiceItem.objects.create(
            invoice=self.invoice, product_name='دواء تجريبي', unit_name='قطعة',
            quantity=3, public_price=60, unit_price=50, discount_percent=0,
        )
        self.reversal = InvoiceReversal.objects.create(
            invoice=self.invoice, amount=Decimal('50.00'), created_by=self.admin,
        )

    def test_invoice_print_renders_ok(self):
        self.http.force_login(self.admin)
        response = self.http.get(reverse('invoices:print', args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('MG Pharmacy', content)
        self.assertNotIn('Bio Zone', content)
        self.assertNotIn('بيو زون', content)

    def test_order_print_renders_ok(self):
        self.http.force_login(self.admin)
        response = self.http.get(reverse('staff:order_print', args=[self.order.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('MG Pharmacy', response.content.decode('utf-8'))

    def test_reversal_print_renders_ok(self):
        self.http.force_login(self.admin)
        response = self.http.get(reverse('invoices:reversal_print', args=[self.reversal.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('MG Pharmacy', response.content.decode('utf-8'))

    def test_client_can_print_own_invoice_but_not_others(self):
        other_client = User.objects.create_user(
            username='client2', email='client2@example.com', password='testpass123',
            role=User.Role.CLIENT, status=User.Status.ACTIVE,
        )
        self.http.force_login(self.client_user)
        own = self.http.get(reverse('invoices:print', args=[self.invoice.pk]))
        self.assertEqual(own.status_code, 200)

        self.http.force_login(other_client)
        other = self.http.get(reverse('invoices:print', args=[self.invoice.pk]))
        self.assertEqual(other.status_code, 403)

    def test_invoice_print_shows_configured_phone_only(self):
        self.http.force_login(self.admin)
        with self.settings(INVOICE_COMPANY_PHONE=''):
            response = self.http.get(reverse('invoices:print', args=[self.invoice.pk]))
            self.assertNotIn('num leading-relaxed">\n                        \n', response.content.decode('utf-8'))
        with self.settings(INVOICE_COMPANY_PHONE='0100 000 0000'):
            response = self.http.get(reverse('invoices:print', args=[self.invoice.pk]))
            self.assertIn('0100 000 0000', response.content.decode('utf-8'))

    def test_multi_page_invoice_split_correctly(self):
        # 20 صنف > ITEMS_PER_PRINT_PAGE (14) — لازم تتقسم لصفحتين
        for i in range(20):
            InvoiceItem.objects.create(
                invoice=self.invoice, product_name=f'صنف {i}', unit_name='قطعة',
                quantity=1, public_price=10, unit_price=10, discount_percent=0,
            )
        self.http.force_login(self.admin)
        response = self.http.get(reverse('invoices:print', args=[self.invoice.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('1/2', content)
        self.assertIn('2/2', content)
        # الملخص لازم يظهر مرة واحدة بس (آخر صفحة)
        self.assertEqual(content.count('صافي الفاتورة كتابةً'), 1)
