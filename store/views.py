from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from products.models import Category, Product, ProductUnit
from products.matching import normalize_name
from products.new_arrivals import new_arrival_filter, NEW_ARRIVALS_WINDOW_DAYS
from inventory.models import Inventory
from orders.cart import Cart
from studio.models import LandingPageSettings
from django.db.models import Q, Case, When, Value, BooleanField, Count


def _cart_quantities(request):
    """
    قاموس {unit_id: quantity} للسلة النشطة الحالية — بيتحسب مرة واحدة
    وبيتحقن في شبكة المنتجات عشان بطاقة المنتج تعرف تعرض الـ stepper
    (+/-) للأصناف الموجودة فعلاً بالسلة بدل زرار "أضف" دايمًا. فاضل
    فاضي لغير العميل المسجّل (زائر/موظف) لأنه مالوش سلة أصلًا.
    """
    if request.user.is_authenticated and request.user.role == 'CLIENT':
        return Cart(request).get_quantities()
    return {}


PRODUCTS_PER_PAGE = 24


def _base_products_queryset():
    """
    كل المنتجات النشطة، معلَّم عليها is_new_arrival (badge "وارد جديد" في
    الكارت) — من غير أي استبعاد؛ الصنف الوارد فاضل ظاهر هنا زي أي منتج
    عادي بالظبط (في الشبكة، البحث، والأقسام)، وده الفرق الأساسي عن
    التصميم القديم اللي كان بيشيل الصنف من هنا لحد ما يخرج من "الوارد".
    """
    return (
        Product.objects.filter(is_active=True)
        .annotate(
            is_new_arrival=Case(
                When(new_arrival_filter(), then=Value(True)),
                default=Value(False),
                output_field=BooleanField(),
            )
        )
        .select_related('category', 'inventory', 'image')
        # 'image' زودت من المرحلة 8 (STUDIO_PLAN.md) — كارت المنتج
        # (product_card.html) بيوصل لـ product.image.thumbnail/.image،
        # وده شبكة كاملة (PRODUCTS_PER_PAGE=24 منتج) فمن غيرها N+1 واضح.
        # 'units__discounts' (مش 'units' بس) — لأن كارت المنتج بيحسب السعر
        # بعد الخصم لكل صنف لو site_config.show_discounted_prices مفعّل،
        # وده بيوصل لـ unit.discounts.all() لكل وحدة. من غير الـ prefetch
        # ده، كل منتج في الصفحة (24) كان بيعمل استعلام إضافي منفصل (N+1).
        .prefetch_related('units__discounts')
    )


def _apply_filters(products, request):
    """
    بحث + فلترة (قسم/شركة مصنعة) مشتركة بين المتجر العادي وصفحة الوارد،
    عشان صفحة الوارد تدعم نفس البحث والفلاتر بالظبط (كانت ناقصة قبل كده).
    """
    selected_category = request.GET.get('category', '')
    selected_manufacturer = request.GET.get('manufacturer', '')
    search_q = request.GET.get('q', '').strip()

    if selected_category:
        products = products.filter(category__slug=selected_category)
    if selected_manufacturer:
        products = products.filter(manufacturer=selected_manufacturer)
    if search_q:
        normalized_q = normalize_name(search_q)
        products = products.filter(
            Q(name_ar__icontains=search_q)
            | Q(name_en__icontains=search_q)
            | Q(name_key__icontains=normalized_q)
        )

    return products, selected_category, selected_manufacturer, search_q


def _manufacturers_list():
    return (
        Product.objects.filter(is_active=True)
        .exclude(manufacturer='')
        .values_list('manufacturer', flat=True)
        .distinct()
    )


def _categories_with_counts():
    """
    الأقسام النشطة فقط، معلَّم عليها عدد المنتجات النشطة الفعلي (product_count)
    — تُستخدم في كروت "تصفح حسب الفئة" على الصفحة الرئيسية (المرحلة 4)
    عشان الرقم الظاهر تحت كل قسم يبقى حقيقي من قاعدة البيانات مش hardcoded.
    select_related('image') لتفادي استعلام إضافي لكل قسم وقت عرض صورته
    (studio.StudioImage) في القالب.
    """
    return (
        Category.objects.filter(is_active=True)
        .select_related('image')
        .annotate(product_count=Count('products', filter=Q(products__is_active=True)))
        .order_by('name')
    )


def store_home(request):
    categories = _categories_with_counts()
    products, selected_category, selected_manufacturer, search_q = _apply_filters(
        _base_products_queryset(), request
    )
    manufacturers = _manufacturers_list()

    paginator = Paginator(products, PRODUCTS_PER_PAGE)
    # لو فلتر (فئة/بحث) اتغيّر ورجع صفحة مش موجودة (مثلاً كنت في صفحة 5
    # وبقى الناتج صفحتين بس)، get_page بترجع آخر صفحة صالحة بدل ما تطلع خطأ.
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'products': page_obj,
        'page_obj': page_obj,
        'total_products': paginator.count,
        'categories': categories,
        'manufacturers': manufacturers,
        'selected_category': selected_category,
        'selected_manufacturer': selected_manufacturer,
        'search_q': search_q,
        'grid_url': 'store:home',
        'cart_quantities': _cart_quantities(request),
    }

    if request.headers.get('HX-Request'):
        return HttpResponse(render_to_string('store/partials/product_grid.html', context, request=request))

    return render(request, 'store/home.html', context)


def store_search(request):
    return store_home(request)


FEATURED_PRODUCTS_COUNT = 5


def landing(request):
    """
    صفحة الهبوط التسويقية (Phase 7 — ROADMAP.md) — بتفتح على `/` لكل
    الزوار (مسجّلين أو لأ)، عدا الموظفين (ADMIN/WAREHOUSE) اللي بيتوجّهوا
    مباشرة للوحة التحكم زي ما هو موثّق في config/urls.py:home().

    كل المحتوى هنا حقيقي من قاعدة البيانات — مفيش رقم أو منتج وهمي:
    - categories: نفس _categories_with_counts() المستخدمة في كروت "تصفح
      حسب الفئة" بالصفحة الرئيسية للمتجر (Phase 4) — نفس مصدر الحقيقة.
    - featured_products: أحدث 5 منتجات نشطة (الأصدق مع البيانات المتاحة
      فعليًا — مفيش حقل "الأكثر مبيعًا" في الموديل أصلًا، فالتصميم الأصلي
      اللي فيه عنوان "الأكثر طلبًا" اتغيّر عنوانه لـ"أحدث المنتجات" في
      القالب عشان يبقى ادّعاء صحيح). بتتعرض كعرض بسيط للاطّلاع بس (صورة
      + اسم + سعر، بدون زرار "أضف للسلة") — بيوديك بالضغط على الكارت
      لصفحة المنتج مباشرة. قرار متعمّد: صفحة اللاندينج مقصودة تفضل خفيفة
      وبعيدة عن تعقيد إدارة حالة السلة، والشراء الفعلي بيحصل في المتجر
      (/store/) اللي زرار "تسوق الآن" بيوديك له مباشرة.
    - landing_settings: صور الـ Hero والبانرات الاختيارية (لو الموظف
      اختارهم من الاستوديو) — راجع studio.models.LandingPageSettings.
    """
    categories = _categories_with_counts()
    featured_products = list(_base_products_queryset().order_by('-created_at')[:FEATURED_PRODUCTS_COUNT])
    return render(request, 'landing.html', {
        'categories': categories,
        'featured_products': featured_products,
        'total_products': Product.objects.filter(is_active=True).count(),
        'landing_settings': LandingPageSettings.objects.select_related('hero_image', 'banner_1', 'banner_2').first(),
    })


@login_required
def new_arrivals(request):
    """
    صفحة "الوارد الجديد" — كل منتج جديد أو اتزوّد رصيده خلال آخر
    NEW_ARRIVALS_WINDOW_DAYS يوم (نفس شرط badge الوارد في المتجر العادي
    بالظبط). خاصة بالعملاء المسجّلين فقط (login_required)، وبتدعم نفس
    بحث/فلاتر المتجر العادي (قسم، شركة مصنعة، بحث بالاسم) — الصنف هنا
    فاضل موجود في المتجر العادي كمان، الصفحة دي مجرد تجميعة مفلترة.
    """
    categories = Category.objects.filter(is_active=True)
    base_qs = _base_products_queryset().filter(new_arrival_filter())
    products, selected_category, selected_manufacturer, search_q = _apply_filters(base_qs, request)
    manufacturers = _manufacturers_list()

    paginator = Paginator(products.order_by('-new_arrival_at'), PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'products': page_obj,
        'page_obj': page_obj,
        'total_products': paginator.count,
        'categories': categories,
        'manufacturers': manufacturers,
        'selected_category': selected_category,
        'selected_manufacturer': selected_manufacturer,
        'search_q': search_q,
        'window_days': NEW_ARRIVALS_WINDOW_DAYS,
        'grid_url': 'store:new_arrivals',
        'cart_quantities': _cart_quantities(request),
    }

    if request.headers.get('HX-Request'):
        return HttpResponse(render_to_string('store/partials/product_grid.html', context, request=request))

    return render(request, 'store/new_arrivals.html', context)


def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.filter(is_active=True)
        .select_related('category', 'image')
        .prefetch_related(
            'units__discounts',
            'similar_products__units__discounts', 'similar_products__category', 'similar_products__inventory', 'similar_products__image',
            'complementary_products__units__discounts', 'complementary_products__category', 'complementary_products__inventory', 'complementary_products__image',
        ),
        pk=pk,
    )
    # صفحة منتج واحد بس (مش شبكة متجر)، فمفيش قلق أداء من استعلام إضافي هنا.
    # المرحلة 6: العميل بيختار الوحدة بنفسه دلوقتي (شوف product_detail.html)،
    # فبنمرّر *كل* وحدات المنتج (units_for_selection) بدل وحدة واحدة بس.
    # default_unit لسه بتتحسب زي الأول (units_for_client) — بس دلوقتي
    # معناها "الاختيار المبدئي وقت ما الصفحة تفتح" مش "الوحدة الوحيدة
    # المتاحة"؛ العميل حر يبدّلها لأي وحدة تانية من الأزرار في القالب.
    # ملحوظة: المخزون بقى على مستوى المنتج (product.inventory) مش الوحدة —
    # ما بنعملش وصول مباشر ليه هنا في كود بايثون، لأن منتج جديد لسه ماتفتحش
    # له مخزون هيعمل RelatedObjectDoesNotExist. القالب بيوصل لـ product.inventory
    # بأمان (Django بيتعامل مع الغياب ده silently جوه التمبليت).
    client = request.user if request.user.is_authenticated else None
    units = product.units_for_selection()
    default_unit = product.units_for_client(client)
    default_unit_id = default_unit[0].pk if default_unit else None
    # نفلتر على is_active بايثونيًا (مش .filter() جديد) عشان نستفيد من
    # الـ prefetch_related الجاهز فوق بدل ما نضرب استعلام إضافي لكل قسم.
    similar_products = [p for p in product.similar_products.all() if p.is_active][:6]
    complementary_products = [p for p in product.complementary_products.all() if p.is_active][:6]
    return render(request, 'store/product_detail.html', {
        'product': product,
        'units': units,
        'default_unit_id': default_unit_id,
        'cart_quantities': _cart_quantities(request),
        'similar_products': similar_products,
        'complementary_products': complementary_products,
    })
