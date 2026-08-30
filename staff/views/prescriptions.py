from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from orders.models import PrescriptionRequest
from staff.permissions import perm_required

__all__ = ['prescription_detail']


@perm_required('orders.view_order')
def prescription_detail(request, pk):
    """
    مراجعة طلب روشتة عند المخزن — عرض الصورة (لو موجودة، من خلال view
    محمي منفصل orders:prescription_image راجع orders/storage.py) والنص
    والعنوان وسياسة "لو صنف مش متوفر"، وتغيير status/staff_notes.

    مفيش هنا لسه منطق "تحويل الروشتة لطلب حقيقي بأصناف" — دي مرحلة تالية
    منفصلة متفق عليها قبل كده إنها لاحقة (راجع docstring
    orders.models.PrescriptionRequest). الشاشة دي بس مراجعة وتغيير حالة/
    ملاحظات، نفس صلاحية orders.view_order المستخدمة في باقي شاشات
    الطلبات (notify_new_prescription_request بتستخدم نفس الصلاحية).
    """
    prescription = get_object_or_404(
        PrescriptionRequest.objects.select_related('client', 'address', 'resulting_order'),
        pk=pk,
    )

    if request.method == 'POST':
        new_status = request.POST.get('status')
        valid_statuses = dict(PrescriptionRequest.Status.choices)
        if new_status in valid_statuses:
            prescription.status = new_status
        prescription.staff_notes = request.POST.get('staff_notes', '').strip()
        prescription.save(update_fields=['status', 'staff_notes'])
        messages.success(request, 'تم تحديث طلب الروشتة.')
        return redirect('staff:prescription_detail', pk=prescription.pk)

    return render(request, 'staff/orders/prescription_detail.html', {
        'prescription': prescription,
        'status_choices': PrescriptionRequest.Status.choices,
    })
