"""
استيراد/تصدير المنتجات من وإلى ملفات إكسل. منطق CRUD الأساسي (عرض/إضافة/
تعديل/حذف) منفصل في crud.py — راجع staff/views/products/__init__.py
للتوثيق الكامل لسبب الفصل.

منطق القراءة/التصنيف/الحفظ نفسه (parsing, fuzzy matching, commit) منقول
لـ products.services.import_export عشان يبقى قابل للاختبار من غير ما نمر
بـ request/session — هنا بس تنسيق الـ HTTP request/response وrenders.
"""
import uuid
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db import transaction
from django.db.models import Q

from products.models import Category, Product
from products.matching import normalize_name
from products.new_arrivals import NEW_ARRIVALS_WINDOW_DAYS
from products.services import import_export as import_export_service
from staff.permissions import perm_required
from staff.excel_utils import workbook_response

IMPORT_SESSION_KEY = 'product_import_batch'
IMPORT_ERRORS_SESSION_KEY = 'product_import_last_errors'
# ملحوظة: نتيجة معالجة الاستيراد بالخلفية (Celery) بقت متخزنة في الكاش
# (Redis) بمفتاح مبني على user_id — راجع products/tasks.py
# (import_result_cache_key) — مش في الجلسة زي قبل كده، عشان نتيجة
# استيراد الموظف تفضل مرتبطة بيه هو نفسه مش بـsession_key معيّن (شوف
# import_products_status وimport_products_review تحت).
# مسار مؤقت مشترك بين web-store/web-staff/celery-worker (bind mount واحد،
# راجع docker-compose.yml) — هنا بس بيتخزن الملف المرفوع لحد ما الـ task
# تقراه وتمسحه؛ مش media حقيقي ومش متاح عبر nginx.
IMPORT_TMP_DIR = Path(settings.BASE_DIR) / 'tmp' / 'imports'
# حماية من ملف إكسل ضخم بالغلط (أو مقصود): الدفعة بالكامل بتتخزن مؤقتًا في
# الـ session (قاعدة البيانات) بين شاشة المراجعة وشاشة التأكيد، فملف بعشرات
# الآلاف من الصفوف كان بيعمل صف session ضخم ويشغل الـ worker وقت طويل في
# طلب واحد. الحدين دول سقف منطقي لأي استيراد حقيقي (لو المخزن عنده كتالوج
# أكبر فعلاً، يقسّم الملف على أكتر من دفعة).
IMPORT_MAX_FILE_SIZE_MB = 5
IMPORT_MAX_ROWS = 3000

# نفس فكرة IMPORT_TMP_DIR بالظبط بس للاتجاه المعاكس (بناء ملف تصدير في
# الخلفية عبر Celery — راجع products/tasks.py build_products_export
# ومقدمة import_export.py، البند 2 من تقرير الديون التقنية). مسار مؤقت
# مشترك بين web-staff/celery-worker (نفس ./tmp bind mount في
# docker-compose.yml)، مش media حقيقي ومش متاح عبر nginx.
EXPORT_TMP_DIR = Path(settings.BASE_DIR) / 'tmp' / 'exports'
# حالة بناء ملف التصدير في الخلفية — {'state': 'processing'|'done'|'error',
# 'token': '...' (لو done — اسم الملف العشوائي في EXPORT_TMP_DIR)،
# 'filename': '...' (لو done — اسم التحميل الأصلي)، 'message': '...' (لو error)}.
EXPORT_STATUS_SESSION_KEY = 'product_export_status'

# قبل كده كانت شاشة المراجعة بترندر كل صفوف "هيتحدّث"/"هيتضاف" (لحد آلاف
# السطور مع ملف كبير) في قائمة واحدة من غير أي تقسيم — الحل: نفس Paginator
# المستخدم في قائمة المنتجات العادية (crud.py)، بس بيقرا رقم صفحة مختلف
# لكل قسم (?update_page=.. / ?create_page=..) عشان الاتنين يتقسموا صفحات
# مستقلة عن بعض في نفس الشاشة.
REVIEW_LIST_PAGE_SIZE = 50
# عدد مجموعات الإيرورز (بعد التجميع) المعروضة افتراضيًا قبل الحاجة لـ "عرض الكل".
REVIEW_ERRORS_PREVIEW_COUNT = 15

# صفحة اختيار الأصناف للتصدير (export_products_select) كانت بتسحب كل
# أصناف المتجر دفعة واحدة كـ JSON وتفلتر/تقسّم صفحات في المتصفح بالكامل —
# مع كتالوج كبير ده بيبطّئ فتح الصفحة (تحميل + parse لكل الأصناف حتى لو
# الموظف هيصدّر 5 بس). دلوقتي البحث/الفلترة/تقسيم الصفحات بيحصلوا في
# السيرفر (زي staff:product_list بالظبط) والصفحة الحالية بس اللي بتوصل
# للمتصفح — التحديد نفسه (IDs) لسه متراكم في Alpine مستقل عن أي صفحة
# ظاهرة حاليًا، عشان التنقل بين الصفحات أو تغيير الفلتر ميمسحش أي تحديد
# سابق (نفس السبب اللي كان خلّى التصميم الأصلي يحمّل كل حاجة مرة واحدة).
EXPORT_PICKER_PAGE_SIZE = 50

# Backward-compat: بعض الكود القديم (أو أي كود خارجي) كان بيستورد الثوابت
# دي من هنا مباشرة قبل الفصل — بتفضل متاحة كـ alias للمصدر الحقيقي.
FUZZY_MATCH_THRESHOLD = import_export_service.FUZZY_MATCH_THRESHOLD
DISCOUNT_COL_PREFIX = import_export_service.DISCOUNT_COL_PREFIX


@perm_required('products.add_product')
def import_products(request):
    """
    المرحلة الأولى: حفظ الملف المرفوع وتشغيل قراءته وتصنيفه (تحديث أكيد /
    إضافة جديدة أكيدة / يحتاج مراجعة) في الخلفية عبر Celery — مش جوه طلب
    HTTP نفسه (راجع products/tasks.py — parse_import_file لسبب النقل).
    الفحوصات هنا (الامتداد، حجم الملف) لسه متزامنة لأنها سريعة (بايتات
    قليلة، مفيش قراءة فعلية للملف)؛ القراءة والتصنيف الفعليين (اللي كانا
    بياخدوا وقت مع ملف كبير) بس اللي اتنقلوا. الحفظ الفعلي في قاعدة
    البيانات لسه بيحصل في import_products_confirm بعد موافقة الموظف على
    أي صف يحتاج قرار بشري (اسم قريب من صنف موجود) — ده لم يتغيّر.
    """
    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')
        if not excel_file:
            messages.error(request, 'يرجى اختيار ملف Excel أولاً.')
            return redirect('staff:import_products')
        if not excel_file.name.endswith('.xlsx'):
            messages.error(request, 'يجب أن يكون الملف بصيغة .xlsx')
            return redirect('staff:import_products')
        if excel_file.size > IMPORT_MAX_FILE_SIZE_MB * 1024 * 1024:
            messages.error(
                request,
                f'حجم الملف أكبر من الحد المسموح ({IMPORT_MAX_FILE_SIZE_MB} ميجا). '
                f'يرجى تقسيم الملف على أكتر من دفعة استيراد.'
            )
            return redirect('staff:import_products')

        IMPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = IMPORT_TMP_DIR / f'{uuid.uuid4().hex}.xlsx'
        with open(tmp_path, 'wb') as f:
            for chunk in excel_file.chunks():
                f.write(chunk)

        from products.tasks import import_result_cache_key, parse_import_file
        # نظّف أي نتيجة استيراد سابقة لنفس الموظف (لو رفع ملف قبل كده
        # ولسه فاتح الشاشة) عشان شاشة الانتظار متتأكدش من نتيجة قديمة غلط.
        cache.delete(import_result_cache_key(request.user.pk))
        parse_import_file.delay(str(tmp_path), IMPORT_MAX_ROWS, request.user.pk)

        return render(request, 'staff/products/import_processing.html')
    return render(request, 'staff/products/import.html')


@perm_required('products.add_product')
def import_products_status(request):
    """
    Endpoint خفيف بتستدعيه شاشة الانتظار (import_processing.html) كل
    ثانيتين عن طريق JS بسيط — بيتأكد هل مهمة Celery خلصت (النتيجة موجودة
    في الكاش) ولا لسه. مفيش حاجة تقيلة هنا، مجرد قراءة كاش — عكس الشكل
    القديم اللي كان بيعمل full page reload كامل لصفحة الانتظار نفسها.
    """
    from products.tasks import import_result_cache_key
    cached = cache.get(import_result_cache_key(request.user.pk))
    return JsonResponse({'ready': cached is not None})


@perm_required('products.add_product')
def import_products_review(request):
    """
    شاشة المراجعة: بتعرض عدد الأصناف اللي هتتحدّث/هتتضاف بثقة تلقائيًا،
    وبتوقف عند أي صف اسمه قريب من صنف موجود وتسأل الموظف صراحةً هل ده
    نفس الصنف (تحديث) ولا صنف جديد فعلًا — قبل أي حفظ في قاعدة البيانات.
    """
    batch = request.session.get(IMPORT_SESSION_KEY)
    if not batch:
        # أول ما يوصل هنا (من رابط الإشعار أو تحويلة شاشة الانتظار)،
        # النتيجة لسه في الكاش (Celery حطها هناك، مش في السيشن — راجع
        # products/tasks.py). ننقلها للسيشن مرة واحدة عشان باقي شاشات
        # الاستيراد (التأكيد، الأخطاء) تفضل شغالة زي ما هي بالظبط.
        from products.tasks import import_result_cache_key
        cached = cache.get(import_result_cache_key(request.user.pk))
        if cached and cached.get('status') == 'done' and not cached.get('error_message'):
            batch = {'rows': cached['rows'], 'errors': cached['errors']}
            request.session[IMPORT_SESSION_KEY] = batch
            cache.delete(import_result_cache_key(request.user.pk))
        elif cached:
            messages.error(request, cached.get('error_message') or 'حصل خطأ أثناء قراءة الملف.')
            cache.delete(import_result_cache_key(request.user.pk))
            return redirect('staff:import_products')

    if not batch:
        messages.error(request, 'مفيش عملية استيراد جارية. من فضلك ارفع الملف تاني.')
        return redirect('staff:import_products')

    rows = batch['rows']
    all_update_rows = [r for r in rows if r['action'] == 'update']
    all_create_rows = [r for r in rows if r['action'] == 'create']
    # review_rows (فيها radio inputs لازم تتبعت كلها في الفورم) مش بتتقسّم —
    # طبيعتها محدودة أصلًا (بس الصفوف اللي محتاجة قرار بشري لتشابه الاسم).
    review_rows = [r for r in rows if r['action'] == 'review']

    update_paginator = Paginator(all_update_rows, REVIEW_LIST_PAGE_SIZE)
    update_page = update_paginator.get_page(request.GET.get('update_page'))
    create_paginator = Paginator(all_create_rows, REVIEW_LIST_PAGE_SIZE)
    create_page = create_paginator.get_page(request.GET.get('create_page'))

    grouped_errors = import_export_service.group_import_errors(batch['errors'])

    context = {
        'grouped_errors': grouped_errors,
        'errors_preview_count': REVIEW_ERRORS_PREVIEW_COUNT,
        'update_rows': update_page,
        'update_rows_total': len(all_update_rows),
        'create_rows': create_page,
        'create_rows_total': len(all_create_rows),
        'review_rows': review_rows,
        'new_arrivals_window_days': NEW_ARRIVALS_WINDOW_DAYS,
    }
    return render(request, 'staff/products/import_review.html', context)


@perm_required('products.add_product')
def import_products_confirm(request):
    """
    المرحلة التانية: بتاخد قرارات الموظف على صفوف "المراجعة" (اتحدد لكل
    واحد منها إما تحديث صنف بعينه أو إضافته كصنف جديد فعلًا) وتنفّذ الحفظ
    الفعلي لكل صفوف الدفعة مرة واحدة داخل transaction واحدة.
    """
    if request.method != 'POST':
        return redirect('staff:import_products')

    batch = request.session.get(IMPORT_SESSION_KEY)
    if not batch:
        messages.error(request, 'انتهت صلاحية عملية الاستيراد دي. من فضلك ارفع الملف تاني.')
        return redirect('staff:import_products')

    rows = batch['rows']
    decisions = {
        row['row_num']: request.POST.get(f"decision_{row['row_num']}", 'new')
        for row in rows if row['action'] == 'review'
    }
    try:
        with transaction.atomic():
            created_count, updated_count, restocked_count = import_export_service.commit_import_batch(
                rows, decisions, request.user,
            )
    except Exception as e:
        messages.error(request, f'حصل خطأ أثناء الحفظ ولم يتم حفظ أي صنف: {str(e)}')
        return redirect('staff:import_products')

    del request.session[IMPORT_SESSION_KEY]
    if created_count:
        messages.success(request, f'تم إضافة {created_count} صنف جديد.')
    if updated_count:
        messages.success(request, f'تم تحديث {updated_count} صنف موجود.')
    # قبل كده كان بيتحط toast منفصل لكل سطر فيه مشكلة (ممكن تبقى مئات
    # التوستات فوق بعض مع ملف كبير) — دلوقتي بنحفظ التفاصيل الكاملة
    # (مجمّعة حسب نوع المشكلة) في الـ session ونوجّه الموظف لصفحة مخصصة
    # يراجعها فيها براحته، بدل ما تتفرقع كلها كـ toasts فوق بعض.

    # إشعار العملاء بالوارد الجديد — اختياري، الموظف بيحدده من شاشة المراجعة.
    # new_arrival_at اتحدّث تلقائيًا لكل صنف جديد أو اتزوّد رصيده (راجع
    # Product.save و StockMovement.save)، فمفيش داعي نحسب حاجة تانية هنا —
    # بس نتأكد إن فيه فعلاً حاجة جديدة تستاهل إشعار قبل ما نبعته.
    new_arrivals_total = created_count + restocked_count
    if request.POST.get('notify_clients') == 'on' and new_arrivals_total > 0:
        from notifications.services import notify_all_clients
        notify_all_clients(
            kind='NEW_ARRIVALS',
            title='وارد جديد في المتجر 🆕',
            message=f'تم إضافة {new_arrivals_total} صنف جديد أو تزويد رصيده — اطّلع على صفحة الوارد.',
            url_name='store:new_arrivals',
        )
        messages.success(request, 'تم إرسال إشعار الوارد الجديد لكل العملاء.')

    if batch['errors']:
        request.session[IMPORT_ERRORS_SESSION_KEY] = batch['errors']
        messages.warning(request, f'تم تجاهل {len(batch["errors"])} صف فيهم مشكلة أثناء القراءة — التفاصيل تحت.')
        return redirect('staff:import_products_errors')

    return redirect('staff:product_list')


@perm_required('products.add_product')
def import_products_errors(request):
    """
    تفاصيل التحذيرات (صفوف اتجاهلت) بعد آخر عملية استيراد اتأكّدت — بديل
    عن رميهم كـ toasts منفصلة. مجمّعة حسب نوع المشكلة (راجع
    group_import_errors) ومقسّمة صفحات لو كانت المجموعات كتير.
    """
    errors = request.session.get(IMPORT_ERRORS_SESSION_KEY, [])
    grouped_errors = import_export_service.group_import_errors(errors)
    paginator = Paginator(grouped_errors, REVIEW_LIST_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'staff/products/import_errors.html', {
        'grouped_errors': page_obj,
        'total_errors': len(errors),
        'total_groups': paginator.count,
    })


@perm_required('products.view_product')
def download_template(request):
    """
    قالب فيه صف لكل وحدة (مش صف لكل صنف) — راجع
    products.services.import_export.build_import_template_workbook لتفاصيل الصيغة.
    """
    wb = import_export_service.build_import_template_workbook()
    return workbook_response(wb, 'biozone_products_template.xlsx')


@perm_required('products.view_product')
def export_products(request):
    """
    تصدير كل الأصناف الحالية دفعة واحدة (بدون اختيار). زي الاستيراد
    بالظبط: بناء الملف نفسه (سحب كل الأصناف + كتابة الإكسل) بيتنفذ في
    الخلفية عبر Celery (products/tasks.py — build_products_export) مش
    جوه طلب HTTP، عشان مع نمو الكتالوج ميبقاش نفس عنق الزجاجة اللي كان
    في الاستيراد قبل النقل (راجع تقرير الديون التقنية، البند 2).
    """
    if not request.session.session_key:
        request.session.save()

    request.session[EXPORT_STATUS_SESSION_KEY] = {'state': 'processing'}
    from products.tasks import build_products_export
    build_products_export.delay(
        request.session.session_key, None, 'mgpharmacy_products_export.xlsx', request.user.pk,
    )
    return redirect('staff:export_products_processing')


@perm_required('products.view_product')
def export_products_processing(request):
    """
    شاشة انتظار بينما build_products_export شغالة في celery-worker — لسه
    على نمط الجلسة + WebSocket + full page reload القديم (بعكس الاستيراد
    اللي بقى بيستخدم كاش + fetch خفيف، راجع import_products_status
    وproducts/tasks.py — parse_import_file للتفاصيل)، بتحوّل لتحميل
    الملف الجاهز بدل شاشة مراجعة.
    """
    status = request.session.get(EXPORT_STATUS_SESSION_KEY)
    if not status:
        return redirect('staff:product_list')
    if status.get('state') == 'done':
        return redirect('staff:export_products_download', token=status['token'])
    if status.get('state') == 'error':
        del request.session[EXPORT_STATUS_SESSION_KEY]
        messages.error(request, status.get('message', 'حصل خطأ أثناء بناء ملف التصدير.'))
        return redirect('staff:product_list')
    return render(request, 'staff/products/export_processing.html')


@perm_required('products.view_product')
def export_products_download(request, token):
    """
    بتقدّم ملف التصدير الجاهز للتحميل. التوكن في الرابط لازم يطابق التوكن
    المخزّن في جلسة الموظف نفسه (EXPORT_STATUS_SESSION_KEY) — حماية من
    تخمين مسار ملف موظف تاني، حتى لو EXPORT_TMP_DIR أصلًا مش متاح عبر
    nginx. بعد التقديم، بنمسح الملف من القرص ونشيل الحالة من الجلسة —
    مفيش داعي يفضل محتفظ بيه بعد أول تحميل.
    """
    status = request.session.get(EXPORT_STATUS_SESSION_KEY)
    if not status or status.get('state') != 'done' or status.get('token') != token:
        raise Http404

    path = EXPORT_TMP_DIR / f'{token}.xlsx'
    if not path.exists():
        del request.session[EXPORT_STATUS_SESSION_KEY]
        raise Http404

    del request.session[EXPORT_STATUS_SESSION_KEY]
    response = FileResponse(
        open(path, 'rb'), as_attachment=True, filename=status['filename'],
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    try:
        path.unlink()
    except OSError:
        pass
    return response


def _export_picker_queryset(request):
    """
    الاستعلام المشترك بين صفحة الاختيار (أول تحميل) وendpoint الجدول
    (htmx بعد أي بحث/فلتر/تنقل صفحات) — عشان الاتنين يفلتروا بنفس
    المنطق بالظبط. نفس حقول البحث المستخدمة في staff:product_list
    (name_ar/name_en/name_key المُطبَّع/code) عشان سلوك متسق في كل
    شاشات المنتجات.
    """
    q = request.GET.get('q', '').strip()
    category_slug = request.GET.get('category', '').strip()
    products = Product.objects.select_related('category').order_by('name_ar')
    if category_slug:
        products = products.filter(category__slug=category_slug)
    if q:
        normalized_q = normalize_name(q)
        products = products.filter(
            Q(name_ar__icontains=q)
            | Q(name_en__icontains=q)
            | Q(name_key__icontains=normalized_q)
            | Q(code__icontains=q)
        )
    return products, q, category_slug


def _export_picker_page_context(request):
    products, q, category_slug = _export_picker_queryset(request)
    paginator = Paginator(products, EXPORT_PICKER_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    return {
        'page_obj': page_obj,
        'page_ids': [p.pk for p in page_obj],
        'query': q,
        'category_slug': category_slug,
    }


@perm_required('products.view_product')
def export_products_select(request):
    """
    صفحة اختيار الأصناف قبل التصدير: تقدر تبحث وتحدد أصناف بعينها، أو
    تحدد قسم كامل (وبعدين تشيل منه أي صنف مش عايزه)، والنظام هيصدّر بس
    اللي محدد فعليًا لملف إكسل بنفس صيغة "تصدير الأصناف الحالية".

    الجدول نفسه (صفحة 1 افتراضيًا) بيترندر هنا بنفس الـ partial اللي
    endpoint الـ htmx (export_products_table) بيستخدمه، عشان الصفحة
    الأولى تظهر فورًا من غير طلب htmx إضافي بعد التحميل.
    """
    categories = Category.objects.filter(is_active=True).order_by('name')
    context = _export_picker_page_context(request)
    context['categories'] = categories
    return render(request, 'staff/products/export_select.html', context)


@perm_required('products.view_product')
def export_products_table(request):
    """
    partial الجدول + التقسيم لصفحات — بيترجع نفس الجزء اللي export_products_select
    بيرندره أول مرة، بس مستدعى عن طريق htmx بعد أي تغيير في البحث/الفلتر/
    رقم الصفحة (شوف hx-get في export_select.html).
    """
    context = _export_picker_page_context(request)
    return render(request, 'staff/products/partials/export_table.html', context)


@perm_required('products.view_product')
def export_products_category_ids(request):
    """
    كل IDs الأصناف في قسم معيّن (مش بس اللي ظاهرة في الصفحة الحالية) —
    بيُستخدم لزرار "تحديد القسم كامل" عشان يضيف القسم بالكامل للتحديد
    المتراكم حتى لو القسم بيمتد على أكتر من صفحة. زي التصميم الأصلي،
    مش بيتأثر بخانة البحث — تحديد قسم كامل يعني القسم كله بغض النظر عن
    أي فلتر بحث حالي.
    """
    category_slug = request.GET.get('category', '').strip()
    if not category_slug:
        return JsonResponse({'ids': []})
    ids = list(
        Product.objects.filter(category__slug=category_slug).values_list('pk', flat=True)
    )
    return JsonResponse({'ids': ids})


@perm_required('products.view_product')
def export_products_selected(request):
    """
    يستقبل قائمة IDs من صفحة الاختيار ويصدّرها كملف إكسل واحد. نفس نقل
    export_products للخلفية عبر Celery (راجع تعليقها فوق) — هنا كمان
    مهم حتى لو العدد المحدد صغير عادةً، لأن نفس الـ task قابلة لإعادة
    الاستخدام (product_ids بدل None) بغض النظر عن حجم التحديد.
    """
    if request.method != 'POST':
        return redirect('staff:export_products_select')

    ids = [pk for pk in request.POST.getlist('product_ids') if pk.isdigit()]
    if not ids:
        messages.warning(request, 'لازم تحدد صنف واحد على الأقل قبل التصدير.')
        return redirect('staff:export_products_select')

    if not request.session.session_key:
        request.session.save()

    request.session[EXPORT_STATUS_SESSION_KEY] = {'state': 'processing'}
    from products.tasks import build_products_export
    build_products_export.delay(
        request.session.session_key, ids, 'mgpharmacy_products_export_selected.xlsx', request.user.pk,
    )
    return redirect('staff:export_products_processing')
