"""
تهيئة Celery — تنفيذ العمليات الثقيلة في worker منفصل تمامًا عن Gunicorn،
عشان طلب HTTP يرجع فورًا من غير ما ياخد worker كامل لمدة طويلة.

منقول من Biozone (المصدر الأصلي لهذا الفرع). المهام المسجّلة حاليًا:
products/tasks.py (استيراد/تصدير ملفات المنتجات)، staff/tasks.py (النسخ
الاحتياطي اليدوي وتصدير التقارير)، وnotifications/tasks.py (نشر الإشعار
الجماعي — fanout_trim_and_push_task). app.autodiscover_tasks() تحت
بيلاقيها تلقائيًا من غير أي تسجيل يدوي.

نفس Redis المستخدم أصلاً كـ CACHES/CHANNEL_LAYERS (راجع settings.py)
بيتستخدم هنا كـ broker + result backend على قاعدة بيانات منفصلة، من غير
حاجة لخدمة إضافية غير Redis نفسه.
"""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('mgpharmacy')
# namespace='CELERY' يعني أي إعداد في settings.py اسمه بادئ بـ CELERY_
# (زي CELERY_BROKER_URL) بيتقرا تلقائيًا هنا من غير تكرار.
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
