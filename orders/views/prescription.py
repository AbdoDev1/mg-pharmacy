from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

from accounts.models import Address

from ..forms import PrescriptionRequestForm
from .decorators import client_required

__all__ = ['prescription_upload']


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
