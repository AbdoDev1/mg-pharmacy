from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render

from inventory.models import Inventory

from ..cart import Cart
from ..models import Order, OrderItem, get_effective_min_order_amount
from .decorators import client_required

__all__ = ['checkout']


@client_required
def checkout(request):
    cart = Cart(request)
    items = cart.get_items()

    if not items:
        messages.warning(request, 'سلة المشتريات فارغة.')
        return redirect('orders:cart')

    min_order_amount = get_effective_min_order_amount(getattr(request.user, 'client_profile', None))
    total = cart.get_total()
    # الطلب مبقاش بيترفض هنا لو إجماليه أقل من الحد الأدنى — بيتبعت عادي
    # للمخزن كطلبية زي أي طلبية تانية، مع تنبيه واضح في صفحة الطلب بالمخزن
    # (Order.is_below_min_order) بدل ما نمنع العميل من الإرسال خالص. المخزن
    # بعد كده يقدر يكمّل الطلب زي ما هو أو يضيف "مصاريف توصيل" لتغطية الفرق.

    client_profile = getattr(request.user, 'client_profile', None)
    # قيم افتراضية لحقول التوصيل — بتتاخد من بروفايل العميل لو متوفرة عشان
    # مايضطرش يكتبها من الصفر كل مرة، لكن تفضل قابلة للتعديل هنا (ممكن
    # يوصّل الطلب ده لعنوان تاني). لو البروفايل متعملوش عليه توثيق أصلًا
    # (عنوان فاضي من فورم التسجيل المبسّط) هتفضل الحقول فاضية والعميل لازم
    # يملاها يدويًا.
    default_name = (client_profile.business_name if client_profile else '') or request.user.get_full_name() or request.user.username
    default_phone = client_profile.phone if client_profile else ''
    default_address = client_profile.address if client_profile else ''

    if request.method == 'POST':
        delivery_name = request.POST.get('delivery_name', '').strip()
        delivery_phone = request.POST.get('delivery_phone', '').strip()
        delivery_address = request.POST.get('delivery_address', '').strip()

        missing = []
        if not delivery_name:
            missing.append('اسم المستلم')
        if not delivery_phone:
            missing.append('رقم الهاتف')
        if not delivery_address:
            missing.append('عنوان التوصيل')

        if missing:
            messages.error(request, f'برجاء إدخال بيانات التوصيل الناقصة: {"، ".join(missing)}.')
            return render(request, 'orders/checkout.html', {
                'cart_items': items,
                'total': total,
                'delivery_name': delivery_name or default_name,
                'delivery_phone': delivery_phone or default_phone,
                'delivery_address': delivery_address or default_address,
                'notes': request.POST.get('notes', ''),
            })

        # ملحوظة: الطلب هنا لا يحجز ولا يخصم أي كمية من المخزون — بيتسجّل بس
        # في حالة "PENDING" لحد ما المخزن يراجعه ويأكده. الفحص تحت للكمية
        # المتاحة هو تنبيه للعميل بس (تجربة استخدام)، مش قفل فعلي على
        # المخزون؛ ممكن الكمية تتغيّر لحد ما المخزن يراجع الطلب فعليًا.
        product_ids = [item['unit'].product_id for item in items]
        inventories = {
            inv.product_id: inv
            for inv in Inventory.objects.filter(product_id__in=product_ids)
        }

        shortages = []
        for item in items:
            unit = item['unit']
            stock_qty = item['quantity'] * unit.qty_in_small
            item['stock_qty'] = stock_qty
            inv = inventories.get(unit.product_id)
            available = inv.available if inv else 0
            if stock_qty > available:
                shortages.append(f"{unit.product.display_name} ({unit.name}): متاح {available // unit.qty_in_small} {unit.name} فقط")

        if shortages:
            for s in shortages:
                messages.error(request, f'الكمية غير متوفرة — {s}')
            return redirect('orders:cart')

        with transaction.atomic():
            order = Order.objects.create(
                client=request.user,
                notes=request.POST.get('notes', ''),
                min_order_amount_snapshot=min_order_amount or None,
                delivery_name=delivery_name,
                delivery_phone=delivery_phone,
                delivery_address=delivery_address,
            )
            for item in items:
                OrderItem.objects.create(
                    order=order,
                    product_unit=item['unit'],
                    quantity=item['quantity'],
                    public_price=item['public_price'],
                    discount_percent=item['discount_percent'],
                    unit_price=item['unit_price'],
                )
        # السلة دي اتحولت لطلب فعليًا، فمفيش داعي تفضل موجودة كسلة فاضية —
        # لو كانت هي السلة النشطة، مفيش أي إعادة إنشاء تلقائية هنا؛ سلة
        # جديدة هتتنشئ بس لو العميل ضاف صنف فعلي تاني.
        if cart.cart_obj is not None:
            cart.cart_obj.delete()
        messages.success(request, f'تم إرسال طلبك رقم #{order.pk} بنجاح!')
        return redirect('orders:order_detail', pk=order.pk)

    return render(request, 'orders/checkout.html', {
        'cart_items': items,
        'total': total,
        'delivery_name': default_name,
        'delivery_phone': default_phone,
        'delivery_address': default_address,
    })
