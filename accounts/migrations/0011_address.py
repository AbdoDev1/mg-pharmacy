from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0010_create_retail_account_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='Address',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(blank=True, help_text='مثلاً: المنزل، الشغل (اختياري)', max_length=100, verbose_name='اسم العنوان')),
                ('full_address', models.TextField(verbose_name='العنوان بالتفصيل')),
                ('is_default', models.BooleanField(default=False, verbose_name='العنوان الافتراضي')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='addresses', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'عنوان',
                'verbose_name_plural': 'العناوين',
                'ordering': ['-is_default', '-created_at'],
            },
        ),
    ]
