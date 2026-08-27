from django import forms

from .models import PrescriptionRequest


class PrescriptionRequestForm(forms.ModelForm):
    """
    فورم رفع الروشتة — العميل لازم يبعت صورة أو وصف كتابي على الأقل
    (الفحص ده في PrescriptionRequest.clean، بيتنادى من الـ view وقت
    full_clean()، مش هنا في الفورم عشان الحقلين نفسهم مش required=True
    فرديًا).
    """
    # الحقول كلها متعمولة يدويًا في القالب (radio buttons مخصصة بنفس
    # الأسماء)، مش عن طريق rendering تلقائي للفورم، فمفيش داعي لتحديد
    # widgets هنا — بس الفورم لسه هو مصدر الـ validation الحقيقي.
    class Meta:
        model = PrescriptionRequest
        fields = ['address', 'image', 'text_description', 'unavailable_policy']

    def __init__(self, *args, addresses=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['address'].queryset = addresses if addresses is not None else self.fields['address'].queryset.none()
        self.fields['address'].empty_label = None
        self.fields['image'].required = False
        self.fields['text_description'].required = False
