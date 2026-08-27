from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0011_address'),
        ('orders', '0015_order_delivery_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='PrescriptionRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('image', models.ImageField(blank=True, null=True, upload_to='prescriptions/%Y/%m/', verbose_name='صورة الروشتة')),
                ('text_description', models.TextField(blank=True, verbose_name='اسم الدواء أو وصف الطلب كتابةً')),
                ('unavailable_policy', models.CharField(choices=[('SUBSTITUTE', 'اختيار بديل إن وجد'), ('PARTIAL', 'توصيل الطلب بدون المنتج الناقص'), ('CANCEL', 'إلغاء الطلب بالكامل')], default='PARTIAL', max_length=20, verbose_name='لو صنف مش متوفر')),
                ('status', models.CharField(choices=[('PENDING', 'قيد المراجعة'), ('PROCESSING', 'جاري التجهيز'), ('FULFILLED', 'تم التحويل لطلب'), ('CANCELLED', 'ملغاة')], default='PENDING', max_length=20)),
                ('staff_notes', models.TextField(blank=True, verbose_name='ملاحظات المخزن')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('address', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='prescription_requests', to='accounts.address', verbose_name='عنوان التوصيل')),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prescription_requests', to=settings.AUTH_USER_MODEL)),
                ('resulting_order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='prescription_request', to='orders.order')),
            ],
            options={
                'verbose_name': 'طلب روشتة',
                'verbose_name_plural': 'طلبات الروشتات',
                'ordering': ['-created_at'],
            },
        ),
    ]
