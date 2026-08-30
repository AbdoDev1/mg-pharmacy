from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrescriptionImageStorage(FileSystemStorage):
    """
    تخزين منفصل لصور الروشتات — برّه MEDIA_ROOT اللي nginx بيعرضه عام
    بالكامل بلا أي auth (راجع nginx/nginx.conf: location /media/، وراجع
    SECURITY_REPORT.md للتفاصيل الكاملة). صور الروشتة مستند طبي شخصي
    (فيه غالبًا اسم العميل وأدويته)، فمينفعش تتقدّم زي صور المنتجات
    العادية — لازم تعدّي بس من خلال orders:prescription_image (view
    بايثون محمي بيتحقق إن الطالب هو نفسه صاحب الروشتة أو موظف عنده
    orders.view_order، راجع orders/views/prescription.py).

    base_url=None عن قصد: أي كود ينادي .image.url بالغلط هيرمي Exception
    فورًا بدل ما يرجّع رابط عام واهم بيسرّب الملف — الطريقة الوحيدة
    الصحيحة لعرض الصورة هي عن طريق الـ view المحمي.
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('location', settings.PRESCRIPTIONS_ROOT)
        kwargs.setdefault('base_url', None)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        # عشان makemigrations يقدر يسجّل استخدام الكلاس ده كـ storage
        # للحقل من غير ما يحاول يسجّل location/base_url كـ kwargs زيادة
        # (هما بالفعل ثابتين من __init__ فوق).
        path, args, kwargs = super().deconstruct()
        kwargs.pop('location', None)
        kwargs.pop('base_url', None)
        return path, args, kwargs
