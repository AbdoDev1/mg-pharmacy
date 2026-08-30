from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Address

from ..forms import PrescriptionRequestForm
from ..models import PrescriptionRequest
from .decorators import client_required

__all__ = ['prescription_upload', 'prescription_image']


@client_required
def prescription_upload(request):
    addresses = Address.objects.filter(client=request.user)

    if request.method == 'POST':
        # لو العميل ملى فورم "عنوان جديد" جوه نفس الصفحة، بنسجله كعنوان
        # في دفتره أول حاجة، وبعدين نستخدمه كعنوان الروشتة — كل ده POST
        # واحد، مفيش رحلة تانية للسيرفر. لو مليش، بناخد العنوان اللي
        # اختاره من العناوين المحفوظة زي ما هو.
        new_address_full = request.POST.get('new_address_full', '').strip()
        if new_address_full:
            address = Address.objects.create(
                client=request.user,
                label=request.POST.get('new_address_label', '').strip(),
                full_address=new_address_full,
                is_default=bool(request.POST.get('new_address_default')),
            )
            addresses = Address.objects.filter(client=request.user)
            post_data = request.POST.copy()
            post_data['address'] = address.pk
        else:
            post_data = request.POST

        form = PrescriptionRequestForm(post_data, request.FILES, addresses=addresses)
        if form.is_valid():
            prescription = form.save(commit=False)
            prescription.client = request.user
            try:
                prescription.full_clean()
            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, error)
            else:
                prescription.save()
                messages.success(
                    request,
                    'تم استلام طلب الروشتة بنجاح، هيتم مراجعته والتواصل معاك قريبًا.',
                )
                return redirect('orders:order_list')
        else:
            messages.error(request, 'برجاء مراجعة البيانات المدخلة.')
    else:
        form = PrescriptionRequestForm(addresses=addresses)

    return render(request, 'orders/prescription_upload.html', {
        'form': form,
        'addresses': addresses,
    })


def prescription_image(request, pk):
    """
    الطريقة المحمية الوحيدة لعرض صورة روشتة — الصورة متخزنة برّه
    MEDIA_ROOT العام (راجع orders/storage.py وSECURITY_REPORT.md)، فمفيش
    رابط مباشر ليها أصلًا (.image.url هترمي Exception). هنا بنتحقق يدويًا
    قبل ما نقرا الملف من القرص ونرجّعه: إما (أ) نفس العميل صاحب الروشتة،
    أو (ب) موظف عنده صلاحية orders.view_order — نفس الصلاحية المستخدمة في
    staff:prescription_detail وباقي شاشات الطلبات.
    """
    prescription = get_object_or_404(PrescriptionRequest, pk=pk)

    if not request.user.is_authenticated:
        raise Http404
    is_owner = request.user.pk == prescription.client_id
    is_staff_viewer = request.user.has_perm('orders.view_order')
    if not (is_owner or is_staff_viewer):
        raise Http404

    if not prescription.image:
        raise Http404

    return FileResponse(prescription.image.open('rb'))
