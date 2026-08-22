"""
أول مهمة Celery فعلية في MG Pharmacy (راجع config/celery.py — كان celery-worker
شغال وhealthy من غير أي task مسجّلة قبل كده). البند اللي كانت بتحله: قراءة
وتصنيف ملف استيراد المنتجات (staff/views/products/import_export.py) كانت
بتتنفذ بشكل متزامن جوه نفس طلب HTTP وتاخد Gunicorn worker كامل طول مدة
القراءة — ده اللي كان بيسبب 504 من nginx مع ملفات كبيرة نسبيًا (موثّق في
نتائج اختبار baseline بالمرحلة 0).

parse_import_file (تحت) منقولة لتخزين النتيجة في الكاش (Redis) بدل جلسة
الموظف مباشرة — راجع تعليق IMPORT_RESULT_CACHE_PREFIX تحت لسبب النقل ده.
build_products_export لسه بتستخدم الجلسة+WebSocket القديمة (مفيش داعي
لنفس التعديل هنا دلوقتي — نطاق التغيير الحالي الاستيراد بس).
"""
import os
import uuid

from celery import shared_task
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache

# نتيجة قراءة ملف الاستيراد بتتخزن في الكاش بدل الجلسة، لأن الجلسة
# مرتبطة بـsession_key الطلب اللي أنشأها، وممكن (نظريًا) يختلف عن
# session_key المتصفح وقت الـpoll (تجديد كوكي، جلسة جديدة...الخ). التخزين
# بمفتاح مبني على user_id بدل session_key بيقفل الاحتمال ده تمامًا — نتيجة
# استيراد الموظف مرتبطة بيه هو نفسه، مش بجلسة معيّنة. مفتاح لكل موظف عشان
# كل واحد يلاقي نتيجة استيراده هو بس.
IMPORT_RESULT_CACHE_PREFIX = 'product_import_result:'
IMPORT_RESULT_TTL = 60 * 30  # 30 دقيقة — كفاية للموظف يفتح شاشة المراجعة


def import_result_cache_key(user_id):
    return f'{IMPORT_RESULT_CACHE_PREFIX}{user_id}'


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


@shared_task(bind=True, soft_time_limit=600, time_limit=900)
def parse_import_file(self, tmp_path, max_rows, user_id):
    """
    بتتنفذ في celery-worker. بتقرا وتصنّف ملف الإكسل المحفوظ مؤقتًا في
    tmp_path (بنفس دالة القراءة الأصلية read_import_workbook — مفيش أي
    تغيير في منطق التصنيف نفسه، بس نقل مكان تنفيذه)، وتخزّن النتيجة في
    الكاش (مفتاح مبني على user_id — راجع IMPORT_RESULT_CACHE_PREFIX فوق)
    بدل جلسة الموظف زي قبل كده. آخر خطوة: إشعار دائم للموظف عبر
    notifications.services.notify — بيوصله فورًا لو فاتح المتصفح (نفس
    آلية بث الجرس اللحظي المستخدمة لباقي الإشعارات في النظام)، وبيفضل
    منتظره في الجرس لو قفل التاب أو رجع بعد وقت طويل.
    """
    # استيراد داخل الدالة (مش أعلى الملف) عشان نتجنب استيراد دائري بين
    # products.tasks وstaff.views.products.import_export (اللي بيستورد
    # منها) — نفس أسلوب notifications/services.py.
    from products.services import import_export as import_export_service

    try:
        with open(tmp_path, 'rb') as f:
            rows, errors, error_message = import_export_service.read_import_workbook(
                f, max_rows=max_rows,
            )
        result = {'status': 'done', 'rows': rows, 'errors': errors, 'error_message': error_message}
    except Exception as e:
        # بيغطي أي استثناء غير متوقع أثناء القراءة، بما فيها تجاوز مهلة
        # المعالجة (SoftTimeLimitExceeded — استثناء عادي قابل للالتقاط
        # هنا، مش SIGKILL؛ ده بيحصل بس لو اتعدّت مهلة time_limit الصلبة
        # فوق كمان، وهي حالة نادرة جدًا مقارنة بالـsoft limit).
        result = {'status': 'failed', 'error_message': f'حصل خطأ غير متوقع أثناء معالجة الملف: {e}'}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if result['status'] == 'done' and not result.get('rows') and not result.get('error_message'):
        # نفس رسالة المسار القديم بالظبط — التحذيرات التفصيلية (errors)
        # كانت بتتعرض كـ messages.warning منفصلة لكل واحدة؛ هنا بنكتفي
        # بالعدد الإجمالي في رسالة واحدة عشان مفيش request نبعتلها عدة
        # رسايل منفصلة.
        message = 'مفيش أي صف صالح في الملف.'
        if result.get('errors'):
            message += f' ({len(result["errors"])} صف اتجاهل — راجع صيغة القالب.)'
        result = {'status': 'failed', 'error_message': message}

    cache.set(import_result_cache_key(user_id), result, timeout=IMPORT_RESULT_TTL)

    from accounts.models import User
    from notifications.services import notify

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    if result['status'] == 'done' and not result.get('error_message'):
        notify(
            recipient=user,
            kind='IMPORT_READY',
            title='ملف الاستيراد جاهز للمراجعة',
            message=f"تم تجهيز {len(result['rows'])} صف — افتح شاشة المراجعة.",
            url_name='staff:import_products_review',
        )
    else:
        notify(
            recipient=user,
            kind='IMPORT_READY',
            title='مشكلة في قراءة ملف الاستيراد',
            message=result.get('error_message') or 'حصل خطأ غير متوقع أثناء قراءة الملف.',
            url_name='staff:import_products',
        )


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
