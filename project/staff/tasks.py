"""
أول (ولسه الوحيدة) مهمة Celery في تطبيق staff. البند اللي بتحله: زرار
"تشغيل نسخة احتياطية الآن" (staff/views/backup.py — backup_run_now) كان
بينادي perform_backup() مباشرة جوه الـ view، فبياخد worker Gunicorn كامل
طول مدة النسخ (راجع تقرير الديون التقنية، البند 6). التعليق الأصلي في
staff/services/backup.py بيقول إن العملية بتاخد "ثواني معدودة لحجم
القاعدة الحالي"، فالخطورة كانت محدودة، لكنها هتكبر مع نمو القاعدة —
نفس الفلسفة اللي خلّت الاستيراد/التصدير ينتقلوا لـ Celery قبل كده.

perform_backup() نفسها ملهاش أي تغيير — لسه هي المسؤولة عن القفل، الحالة
اللحظية عبر WebSocket (STAFF_BROADCAST_GROUP، تشمل running/success/error)،
والإشعار الدائم عند الفشل (راجع staff/services/backup.py). الفرق الوحيد
هنا إن استدعاءها بقى بيحصل من جوه celery-worker مش gunicorn، فطلب الـ
HTTP بتاع الزرار بيرجع فورًا (راجع backup_run_now تحت) بدل ما يستنى.
"""
from celery import shared_task


@shared_task
def run_backup_task():
    from staff.services.backup import perform_backup
    perform_backup()
