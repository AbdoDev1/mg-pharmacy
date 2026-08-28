# خطة تنفيذ الإصلاحات (المعتمدة)

## Context
1. **المشكلة 1**: دالة `merge_orders_with_returns(orders_qs, client, page=1, page_size=20)` في `invoices/models.py` تحوّل جميع الطلبات والمرتجعات إلى Python list وترتبها يدويًا. سنقوم بتحويلها لاستخدام نمط الـ union+index المماثل لما هو موجود في `inventory.py` و `orders.py`، مع إرجاع `Page` object (أو كائن يتوافق مع القوالب الحالية كـ `page_obj`).
2. **المشكلة 2**: تحويل الـ script في `import_processing.html` و `import_committing.html` لاستخدام Alpine.js (`init()` و `destroy()`) مع تنظيف الـ `setInterval` تماماً لتفادي بقاء الـ polling يعمل في الخلفية بعد الانتقال بـ htmx.

## Detailed Plan
1. **`invoices/models.py`**:
   - تحديث دالة `merge_orders_with_returns(orders_qs, client, page=1, page_size=20)`:
     - أخذ الـ IDs و `created_at` من `orders_qs` (بدون تحميل الأصناف كاملة) مع `annotate(kind=Value('order', ...), source_id=F('pk'), source_rank=Value(0, ...))` و `.values('kind', 'source_id', 'source_rank', 'created_at')`.
     - أخذ الـ indexes لمرتجعات العميل (`InvoiceReversal.objects.filter(invoice__order__client=client)`) مع `annotate(kind=Value('return', ...), source_id=F('pk'), source_rank=Value(1, ...))` و `.values('kind', 'source_id', 'source_rank', 'created_at')`.
     - دمج الاثنين بـ `.union(all=True)` وترتيبهما بـ `.order_by('-created_at', 'source_rank', '-source_id')`.
     - تمرير النتيجة لـ `Paginator(union_qs, page_size)` والحصول على `index_page = paginator.get_page(page)`.
     - تجميع IDs الطلبات و IDs المرتجعات للصفحة الحالية فقط.
     - جلب `Order` objects الفعليين للـ IDs المعنية (مع `.prefetch_related('items')`) وجلب `InvoiceReversal` objects الفعليين (مع `.select_related('invoice__order')`).
     - إعادة بناء قائمة `rows` للصفحة الحالية بالترتيب الصحيح (dicts تحتوي `{'kind', 'obj', 'created_at'}`).
     - إنشاء وتوصيل `Page(rows, index_page.number, paginator)` وإرجاعه.
2. **Views التعديل**:
   - `orders/views/order.py`: تحديث استدعاء `merge_orders_with_returns(orders_qs, request.user, page=request.GET.get('page'), page_size=20)` وإرجاع `page_obj`.
   - `accounts/views.py`: تحديث استدعاء `merge_orders_with_returns(orders_qs, request.user, page=request.GET.get('orders_page'), page_size=ORDERS_TAB_PAGE_SIZE)` وإرجاع `orders_page_obj`.
3. **Templates الاستيراد**:
   - `staff/templates/staff/products/import_processing.html`
   - `staff/templates/staff/products/import_committing.html`
   - تطبيق Alpine.js `x-data="importProcessingView()"` مع `init()` و `destroy()`.
