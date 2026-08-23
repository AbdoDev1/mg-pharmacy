"""
أول مهمة Celery فعلية في MG Pharmacy (راجع config/celery.py — كان celery-worker
شغال وhealthy من غير أي task مسجّلة قبل كده). البند اللي كانت بتحله: قراءة
وتصنيف ملف استيراد المنتجات (staff/views/products/import_export.py) كانت
بتتنفذ بشكل متزامن جوه نفس طلب HTTP وتاخد Gunicorn worker كامل طول مدة
القراءة — ده اللي كان بيسبب 504 من nginx مع ملفات كبيرة نسبيًا (موثّق في
نتائج اختبار baseline بالمرحلة 0).

parse_import_file (تحت) منقولة لتخزين النتيجة في الكاش (Redis) بدل جلسة
الموظف مباشرة — راجع تعليق IMPORT_RESULT_CACHE_PREFIX تحت لسبب النقل ده.
build_products_export كانت بتستخدم نفس نمط الجلسة القديم، وده سبب باگ
حقيقي (لوب تحميل ما بينتهيش) لأن SESSION_ENGINE الفعلي هو cached_db بينما
الـtask كانت بتكتب بنسخة الـDB الخام — دلوقتي منقولة لنفس نمط الكاش
(راجع EXPORT_STATUS_CACHE_PREFIX تحت).
"""
import os
import uuid

from celery import shared_task
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


# دفعة الاستيراد وقت شاشة المراجعة (import_products_review) — بعد ما
# parse_import_file تخلص، الدفعة كانت بتتنقل لجلسة الموظف (request.session)
# وتقعد هناك طول ما هو بيتصفح صفحات المراجعة (update_page/create_page)،
# رغم إنها ممكن توصل لآلاف الصفوف (لحد 3000). بما إن SESSION_ENGINE
# الفعلي cached_db، ده كان معناه تحميل/حفظ الدفعة كاملة من Redis في كل
# طلب تنقل صفحات أثناء المراجعة، مش بس لما يوصلها أول مرة. هنا بدل كده:
# مفتاح كاش مخصص، بيتجدد (TTL) في كل مرة الموظف يفتح شاشة المراجعة، عشان
# مراجعة طويلة نسبيًا متنتهيش صلاحيتها لوحدها من نص الطريق.
IMPORT_REVIEW_BATCH_PREFIX = 'product_import_review_batch:'
IMPORT_REVIEW_BATCH_TTL = 60 * 30  # 30 دقيقة، بتتجدد مع كل فتح للمراجعة


def import_review_batch_cache_key(user_id):
    return f'{IMPORT_REVIEW_BATCH_PREFIX}{user_id}'


# نفس فكرة IMPORT_RESULT_CACHE_PREFIX فوق، بس لمرحلة التأكيد/الحفظ
# (import_products_confirm) بدل مرحلة القراءة. الحفظ الفعلي (commit_import_batch)
# كان بيتنفذ متزامن جوه request/response cycle العادي في web-staff — أخطر
# جزء في الاستيراد لأنه بيلف على كل صفوف الدفعة (لحد 3000) جوه transaction
# واحدة، وweb-staff أضيق container في المشروع (0.25 CPU). دلوقتي منقول
# لـcelery-worker بنفس نمط parse_import_file بالظبط: الدفعة (rows +
# decisions + إعدادات الإشعار) بتتخزن في الكاش قبل الـdelay (مش كـargs
# للـtask نفسها) عشان قيم Decimal في row_data['discounts'] (راجع
# parsing.py) مش JSON-serializable، وCELERY_TASK_SERIALIZER='json' —
# تخزين الكاش نفسه (Redis عبر django cache backend) مش بيعاني من نفس
# القيد لأنه مش بيمر على JSON.
IMPORT_COMMIT_PAYLOAD_PREFIX = 'product_import_commit_payload:'
IMPORT_COMMIT_RESULT_PREFIX = 'product_import_commit_result:'
IMPORT_COMMIT_TTL = 60 * 30


def import_commit_payload_cache_key(user_id):
    return f'{IMPORT_COMMIT_PAYLOAD_PREFIX}{user_id}'


def import_commit_result_cache_key(user_id):
    return f'{IMPORT_COMMIT_RESULT_PREFIX}{user_id}'


# نفس فكرة IMPORT_RESULT_CACHE_PREFIX بالظبط، لكن لتصدير المنتجات
# (build_products_export تحت) بدل استيرادهم. كان أصلاً بيستخدم جلسة
# الموظف (session) عبر SessionStore(session_key=...) من
# django.contrib.sessions.backends.db مباشرة — لكن SESSION_ENGINE
# الفعلي للمشروع هو cached_db (راجع config/settings.py)، وده بيعني إن
# أي قراءة لـ request.session في الفيوهات بتتحقق من الكاش (Redis) الأول
# وترجع منه فورًا لو موجود، من غير ما تلمس قاعدة البيانات خالص. الـ task
# هنا كان بيكتب بنسخة الـ DB الخام بس (مش cached_db)، فكتابته 'done' كانت
# بتوصل لقاعدة البيانات ومتوصلش لنسخة الكاش اللي الفيوهات فعليًا بتقرا
# منها — فالحالة القديمة 'processing' كانت بتفضل هي اللي بترجع للأبد
# (لوب تحميل ما بينتهيش، حتى لو الملف خلص وجاهز فعليًا على القرص). نفس
# حل الاستيراد بالظبط: مفتاح كاش مبني على user_id، بعيد عن أي session
# backend خالص.
EXPORT_STATUS_CACHE_PREFIX = 'product_export_status:'
EXPORT_STATUS_TTL = 60 * 30  # 30 دقيقة — كفاية للموظف يفتح شاشة التحميل


def export_status_cache_key(user_id):
    return f'{EXPORT_STATUS_CACHE_PREFIX}{user_id}'


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


@shared_task(bind=True, soft_time_limit=900, time_limit=1200)
def commit_import_batch_task(self, user_id):
    """
    بتتنفذ في celery-worker. بتاخد الدفعة (rows + decisions + notify_clients)
    اللي import_products_confirm خزّنها في الكاش قبل الـdelay (راجع
    IMPORT_COMMIT_PAYLOAD_PREFIX فوق)، وتنفّذ commit_import_batch الفعلية
    جوه transaction واحدة — بالظبط زي ما كانت بتحصل قبل كده جوه الـview،
    بس هنا مش شغالة جوه worker web-staff (0.25 CPU) ومش مربوطة بمهلة
    nginx/gunicorn لطلب HTTP حي. مهلة أطول من parse_import_file (15 دقيقة
    soft / 20 دقيقة صلبة) لأن الحفظ الفعلي (كتابة + validation لكل صف)
    أبطأ جوهريًا من مجرد القراءة.

    النتيجة بتتخزن في الكاش (نفس نمط parse_import_file) — import_products_
    commit_result بيقراها ويحوّلها لـmessages حقيقية (لازم request فعلي،
    مش متاح جوه task)، ويتعامل مع notify_clients / أخطاء القراءة المرحّلة
    من مرحلة parse نفسها.
    """
    from django.db import transaction

    from accounts.models import User
    from notifications.services import notify
    from notifications.models import Notification
    from products.services import import_export as import_export_service

    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return

    payload = cache.get(import_commit_payload_cache_key(user_id))
    cache.delete(import_commit_payload_cache_key(user_id))

    if not payload:
        result = {
            'status': 'failed',
            'error_message': 'انتهت صلاحية عملية الاستيراد دي. من فضلك ارفع الملف تاني.',
        }
        cache.set(import_commit_result_cache_key(user_id), result, timeout=IMPORT_COMMIT_TTL)
        notify(
            recipient=user, kind=Notification.Kind.IMPORT_COMMITTED,
            title='مشكلة في حفظ الاستيراد', message=result['error_message'],
            url_name='staff:import_products',
        )
        return

    try:
        with transaction.atomic():
            created_count, updated_count, restocked_count = import_export_service.commit_import_batch(
                payload['rows'], payload['decisions'], user,
            )
    except Exception as e:
        # نفس رسالة الخطأ اللي كانت في الـview قبل كده بالظبط — الـ
        # transaction بتتعمل rollback تلقائيًا (نفس ضمان "صفر حفظ جزئي"
        # اللي كان موجود وهي جوه request عادي).
        result = {
            'status': 'failed',
            'error_message': f'حصل خطأ أثناء الحفظ ولم يتم حفظ أي صنف: {e}',
        }
        cache.set(import_commit_result_cache_key(user_id), result, timeout=IMPORT_COMMIT_TTL)
        notify(
            recipient=user, kind=Notification.Kind.IMPORT_COMMITTED,
            title='فشل حفظ الاستيراد', message=result['error_message'],
            url_name='staff:import_products',
        )
        return

    # إشعار العملاء بالوارد الجديد — نفس منطق الـview القديم بالظبط، منقول
    # هنا لأن created_count/restocked_count مش معروفين إلا بعد الحفظ.
    new_arrivals_total = created_count + restocked_count
    if payload.get('notify_clients') and new_arrivals_total > 0:
        from notifications.services import notify_all_clients
        notify_all_clients(
            kind='NEW_ARRIVALS',
            title='وارد جديد في المتجر 🆕',
            message=f'تم إضافة {new_arrivals_total} صنف جديد أو تزويد رصيده — اطّلع على صفحة الوارد.',
            url_name='store:new_arrivals',
        )

    result = {
        'status': 'done',
        'created_count': created_count,
        'updated_count': updated_count,
        'restocked_count': restocked_count,
        'errors': payload.get('errors') or [],
    }
    cache.set(import_commit_result_cache_key(user_id), result, timeout=IMPORT_COMMIT_TTL)

    notify(
        recipient=user, kind=Notification.Kind.IMPORT_COMMITTED,
        title='تم حفظ الاستيراد',
        message=f'تم إضافة {created_count} صنف وتحديث {updated_count} صنف.',
        url_name='staff:import_products_errors' if result['errors'] else 'staff:product_list',
    )


@shared_task(bind=True)
def build_products_export(self, product_ids, filename, user_id):
    """
    نظير parse_import_file بس للاتجاه المعاكس: بناء ملف تصدير المنتجات
    (export_products / export_products_selected) كان بيحصل بشكل متزامن
    جوه نفس طلب HTTP — نفس النمط اللي سبب مشكلة الاستيراد قبل ما تتنقل
    لـ Celery (راجع mg-pharmacy-tech-debt-audit.md، البند 2).

    product_ids: None = كل الأصناف (export_products)، أو قائمة IDs محددة
    (export_products_selected). الملف بيتكتب في مسار مؤقت مشترك (راجع
    staff/views/products/import_export.py — EXPORT_TMP_DIR، نفس مونت
    ./tmp المستخدم للاستيراد) باسم عشوائي (uuid) عشان نمنع أي تخمين لمسار
    ملف موظف تاني. حالة الانتهاء + اسم التحميل الأصلي بيتخزنوا في الكاش
    (مفتاح مبني على user_id — راجع export_status_cache_key فوق) بدل جلسة
    الموظف زي قبل كده، لنفس سبب نقل نتيجة الاستيراد بالظبط (SESSION_ENGINE
    الفعلي cached_db، وده كان بيخلي أي تحديث من هنا يوصل لقاعدة البيانات
    ومتوصلش لنسخة الكاش اللي الفيوهات فعليًا بتقرا منها). view التحميل هي
    اللي بتتأكد إن التوكن في الرابط مطابق للمخزّن في نتيجة الكاش قبل ما
    تقدّم الملف.
    """
    from staff.views.products.import_export import EXPORT_TMP_DIR
    from products.models import Product
    from products.services import import_export as import_export_service

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

    cache.set(export_status_cache_key(user_id), status, EXPORT_STATUS_TTL)
    _notify_user(user_id, 'export_status', status['state'])
