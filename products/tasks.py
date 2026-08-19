"""
أول مهمة Celery فعلية في MG Pharmacy (راجع config/celery.py — كان celery-worker
شغال وhealthy من غير أي task مسجّلة قبل كده). البند اللي كانت بتحله: قراءة
وتصنيف ملف استيراد المنتجات (staff/views/products/import_export.py) كانت
بتتنفذ بشكل متزامن جوه نفس طلب HTTP وتاخد Gunicorn worker كامل طول مدة
القراءة — ده اللي كان بيسبب 504 من nginx مع ملفات كبيرة نسبيًا (موثّق في
نتائج اختبار baseline بالمرحلة 0).

التصميم: الطلب الأصلي بيحفظ الملف المرفوع في مسار مؤقت مشترك (راجع
staff/views/products/import_export.py — IMPORT_TMP_DIR، مونت مشترك بين
web-store/web-staff/celery-worker في docker-compose.yml) ويرجع فورًا،
والـ task هنا هي اللي بتقرا الملف فعليًا وتخزّن النتيجة في *نفس جلسة*
الموظف اللي رفع الملف (عن طريق session_key، مش request.session مباشرة —
مفيش request أصلًا هنا) عشان شاشة المراجعة (import_products_review)
تلاقيها جاهزة تمامًا زي لو كانت اتحسبت جوه الطلب نفسه. آخر خطوة: بث شخصي
عبر WebSocket للموظف ده بس (مش كل الموظفين زي حالة النسخ الاحتياطي في
staff/services/backup.py — راجعها كمرجع لنفس نمط البث) عشان يعرف إن
المعالجة خلصت من غير ما يعمل refresh يدوي.
"""
import os
import uuid

from celery import shared_task
from django.contrib.sessions.backends.db import SessionStore


def _notify_user(user_id, event_type, status):
    """
    بث شخصي (WebSocket) لموظف واحد بس — نفس أسلوب staff/services/backup.py
    (_broadcast) بالظبط، بس هنا لمجموعة شخصية (notifications_user_<id>)
    بدل مجموعة بث عامة، لأن حالة استيراد/تصدير ملف واحد تخص اللي طلبه بس.
    event_type بيحدد نوع الرسالة اللي consumer.py هيبعتها للمتصفح
    (import_status أو export_status — راجع notifications/consumers.py).
    best-effort زي كل استخدامات channel layer في المشروع: لو Redis واقع
    لحظيًا، النتيجة الحقيقية اتخزّنت في الجلسة بالفعل — شاشة المعالجة
    (import_processing.html / export_processing.html) عندها poll دوري
    كشبكة أمان.
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


@shared_task(bind=True)
def parse_import_file(self, session_key, tmp_path, max_rows, user_id):
    """
    بتتنفذ في celery-worker. بتقرا وتصنّف ملف الإكسل المحفوظ مؤقتًا في
    tmp_path (بنفس دالة القراءة الأصلية read_import_workbook — مفيش أي
    تغيير في منطق التصنيف نفسه، بس نقل مكان تنفيذه)، وتخزّن النتيجة في
    جلسة الموظف تحت نفس المفاتيح اللي كانت staff/views/products/import_export.py
    بتحطها فيها مباشرة قبل النقل.
    """
    # استيراد داخل الدالة (مش أعلى الملف) عشان نتجنب استيراد دائري بين
    # products.tasks وstaff.views.products.import_export (اللي بيستورد
    # منها) — نفس أسلوب notifications/services.py.
    from staff.views.products.import_export import (
        IMPORT_SESSION_KEY,
        IMPORT_STATUS_SESSION_KEY,
    )
    from products.services import import_export as import_export_service

    session = SessionStore(session_key=session_key)

    try:
        with open(tmp_path, 'rb') as f:
            rows, errors, error_message = import_export_service.read_import_workbook(
                f, max_rows=max_rows,
            )
    except Exception as e:
        rows, errors, error_message = [], [], f'خطأ غير متوقع أثناء معالجة الملف: {str(e)}'
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if error_message:
        status = {'state': 'error', 'message': error_message}
    elif not rows:
        # نفس رسالة المسار المتزامن القديم بالظبط — التحذيرات التفصيلية
        # (errors) كانت بتتعرض كـ messages.warning منفصلة لكل واحدة؛ هنا
        # بنكتفي بالعدد الإجمالي في رسالة واحدة عشان مفيش request نبعتلها
        # عدة رسايل منفصلة.
        message = 'مفيش أي صف صالح في الملف.'
        if errors:
            message += f' ({len(errors)} صف اتجاهل — راجع صيغة القالب.)'
        status = {'state': 'error', 'message': message}
    else:
        session[IMPORT_SESSION_KEY] = {'rows': rows, 'errors': errors}
        status = {'state': 'done'}

    session[IMPORT_STATUS_SESSION_KEY] = status
    session.save()
    _notify_user(user_id, 'import_status', status['state'])


@shared_task(bind=True)
def build_products_export(self, session_key, product_ids, filename, user_id):
    """
    نظير parse_import_file بس للاتجاه المعاكس: بناء ملف تصدير المنتجات
    (export_products / export_products_selected) كان بيحصل بشكل متزامن
    جوه نفس طلب HTTP — نفس النمط اللي سبب مشكلة الاستيراد قبل ما تتنقل
    لـ Celery (راجع mg-pharmacy-tech-debt-audit.md، البند 2).

    product_ids: None = كل الأصناف (export_products)، أو قائمة IDs محددة
    (export_products_selected). الملف بيتكتب في مسار مؤقت مشترك (راجع
    staff/views/products/import_export.py — EXPORT_TMP_DIR، نفس مونت
    ./tmp المستخدم للاستيراد) باسم عشوائي (uuid) عشان نمنع أي تخمين لمسار
    ملف موظف تاني. حالة الانتهاء + اسم التحميل الأصلي بيتخزنوا في جلسة
    الموظف (EXPORT_STATUS_SESSION_KEY)، وview التحميل هي اللي بتتأكد إن
    التوكن في الرابط مطابق للمخزّن في جلسته قبل ما تقدّم الملف.
    """
    from staff.views.products.import_export import (
        EXPORT_STATUS_SESSION_KEY,
        EXPORT_TMP_DIR,
    )
    from products.models import Product
    from products.services import import_export as import_export_service

    session = SessionStore(session_key=session_key)

    try:
        products = Product.objects.select_related('category').prefetch_related(
            'units__discounts__account_type',
        )
        if product_ids is not None:
            products = products.filter(pk__in=product_ids)

        wb = import_export_service.build_products_export_workbook(products)

        EXPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        wb.save(str(EXPORT_TMP_DIR / f'{token}.xlsx'))
        status = {'state': 'done', 'token': token, 'filename': filename}
    except Exception as e:
        status = {'state': 'error', 'message': f'خطأ غير متوقع أثناء بناء ملف التصدير: {str(e)}'}

    session[EXPORT_STATUS_SESSION_KEY] = status
    session.save()
    _notify_user(user_id, 'export_status', status['state'])
