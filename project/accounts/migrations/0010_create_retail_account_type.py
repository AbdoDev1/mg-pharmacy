from django.db import migrations

RETAIL_ACCOUNT_TYPE_NAME = 'قطاعي'


def create_retail_account_type(apps, schema_editor):
    AccountType = apps.get_model('accounts', 'AccountType')
    AccountType.objects.get_or_create(
        name=RETAIL_ACCOUNT_TYPE_NAME,
        defaults={'default_unit_size': 'S'},
    )


def delete_retail_account_type(apps, schema_editor):
    # عكس الترحيل بس لو النوع لسه من غير أي عميل أو خصم مرتبط بيه —
    # لو اتربط بحاجة، بنسيبه (مش هدفنا نحذف بيانات حقيقية عند التراجع).
    AccountType = apps.get_model('accounts', 'AccountType')
    account_type = AccountType.objects.filter(name=RETAIL_ACCOUNT_TYPE_NAME).first()
    if account_type is None:
        return
    if not account_type.client_profiles.exists():
        account_type.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0009_clientprofile_min_order_amount'),
    ]

    operations = [
        migrations.RunPython(create_retail_account_type, delete_retail_account_type),
    ]
