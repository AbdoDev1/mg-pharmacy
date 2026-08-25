"""
مهام Celery في تطبيق staff.

run_backup_task: زرار "تشغيل نسخة احتياطية الآن" (staff/views/backup.py —
backup_run_now) كان بينادي perform_backup() مباشرة جوه الـ view، فبياخد
worker Gunicorn كامل طول مدة النسخ (راجع تقرير الديون التقنية، البند 6).
التعليق الأصلي في staff/services/backup.py بيقول إن العملية بتاخد "ثواني
معدودة لحجم القاعدة الحالي"، فالخطورة كانت محدودة، لكنها هتكبر مع نمو
القاعدة — نفس الفلسفة اللي خلّت الاستيراد/التصدير ينتقلوا لـ Celery قبل
كده.

perform_backup() نفسها ملهاش أي تغيير — لسه هي المسؤولة عن القفل، الحالة
اللحظية عبر WebSocket (STAFF_BROADCAST_GROUP، تشمل running/success/error)،
والإشعار الدائم عند الفشل (راجع staff/services/backup.py). الفرق الوحيد
هنا إن استدعاءها بقى بيحصل من جوه celery-worker مش gunicorn، فطلب الـ
HTTP بتاع الزرار بيرجع فورًا (راجع backup_run_now تحت) بدل ما يستنى.
هذه المهمة بتتحط على طابور 'backup' منفصل (راجع config/settings.py —
CELERY_TASK_ROUTES، وdocker-compose.yml — celery-worker-backup) عشان
نسخة احتياطية طويلة ماتأخرش استيراد/تصدير موظف تاني مستني على نفس
الطابور — راجع تعليق CELERY_TASK_ROUTES في settings.py لتفاصيل السبب.

build_report_export_task: بناء ملفات تصدير Excel لتقارير قسم reports —
راجع staff/report_export.py لتفاصيل كاملة (البند 2 من
PROJECT_ANALYSIS_REPORT.md).
"""
import uuid

from celery import shared_task


@shared_task
def run_backup_task():
    from staff.services.backup import perform_backup
    perform_backup()


def _notify_user(user_id, event_type, status):
    """
    بث شخصي (WebSocket) لموظف واحد بس — نفس _notify_user في
    products/tasks.py بالظبط (نفس الفلسفة: أي Celery task بتبني ملف
    إكسل في الخلفية محتاجة تنبّه صاحب الطلب لحظيًا لما تخلص). مكررة هنا
    بدل ما تتستورد من products.tasks عشان staff.tasks تفضل مستقلة عن
    products (نفس مبدأ الفصل المستخدم في staff/services/backup.py —
    _broadcast — لبث آخر مختلف). best-effort زي كل استخدامات channel
    layer في المشروع: لو Redis واقع لحظيًا، شاشة الانتظار عندها poll
    دوري كشبكة أمان (راجع staff/templates/staff/products/export_processing.html).
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            f'notifications_user_{user_id}',
            {'type': event_type, 'status': status},
        )
    except Exception:
        pass


@shared_task(bind=True, soft_time_limit=600, time_limit=900)
def build_report_export_task(self, report_kind, params, user_id):
    """
    بتتنفذ في celery-worker. نظير build_products_export (products/tasks.py)
    بس لتقارير قسم staff/reports.py بدل المنتجات — راجع
    staff/report_export.py لسبب النقل الكامل (البند 2 من
    PROJECT_ANALYSIS_REPORT.md: بعض تصديرات التقارير كانت لسه بتتبني
    بشكل متزامن جوه web-staff).

    params: نسخة request.GET.dict() وقت الطلب الأصلي (staff/views/reports.py
    هي اللي بتلقطها وتحطها في الكاش قبل الـdelay، عشان القيم القابلة
    للتغيير بعد كده مالهاش أي تأثير على تقرير بدأ بناؤه بالفعل).
    """
    from django.core.cache import cache

    from staff.report_export import (
        REPORT_KIND_BUILDERS,
        REPORT_KIND_FILENAMES,
        REPORT_EXPORT_STATUS_TTL,
        report_export_status_cache_key,
    )
    from staff.views.products.import_export import EXPORT_TMP_DIR

    try:
        builder = REPORT_KIND_BUILDERS[report_kind]
        wb = builder(params)

        EXPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        wb.save(str(EXPORT_TMP_DIR / f'{token}.xlsx'))
        status = {
            'state': 'done',
            'token': token,
            'filename': REPORT_KIND_FILENAMES.get(report_kind, 'report.xlsx'),
        }
    except Exception as e:
        status = {'state': 'error', 'message': f'خطأ غير متوقع أثناء بناء ملف التقرير: {str(e)}'}

    cache.set(report_export_status_cache_key(user_id), status, REPORT_EXPORT_STATUS_TTL)
    _notify_user(user_id, 'report_export_status', status['state'])
