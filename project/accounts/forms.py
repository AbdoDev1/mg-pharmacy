from django import forms
from django.contrib.auth.forms import UserCreationForm, PasswordChangeForm
from .models import User, ClientProfile, AccountType

INPUT_CLASSES = (
    'w-full border border-gray-300 rounded-lg px-4 py-2 text-sm '
    'focus:outline-none focus:ring-2 focus:ring-blue-500'
)
# نفس الكلاسات فوق + مساحة إضافية على جهة النهاية (يمين في RTL) عشان
# أيقونة "إظهار كلمة المرور" متتلخبطش مع النص وقت الكتابة.
PASSWORD_INPUT_CLASSES = INPUT_CLASSES + ' pe-10'


class ClientPasswordChangeForm(PasswordChangeForm):
    """
    نفس فورم Django القياسي لتغيير كلمة المرور، بس بعناوين عربية
    وكلاسات Tailwind عشان تتوافق مع باقي فورمات الحساب بدل شكل
    Django الافتراضي.
    """
    old_password = forms.CharField(
        label='كلمة المرور الحالية',
        widget=forms.PasswordInput(attrs={
            'class': PASSWORD_INPUT_CLASSES,
            # x-bind:type بيتقرا كـ خاصية HTML عادية من Django (متعرفش
            # إنها Alpine)، وAlpine بيلقطها من الـ DOM بعد ما الصفحة
            # تحمّل. لازم القالب اللي بيعرض الحقل ده يكون جواه عنصر أب
            # فيه x-data="{ show: false }" عشان show تتعرف. راجع
            # dashboard.html للف اللي بيلف على password_form.
            'x-bind:type': "show ? 'text' : 'password'",
        }),
    )
    new_password1 = forms.CharField(
        label='كلمة المرور الجديدة',
        widget=forms.PasswordInput(attrs={
            'class': PASSWORD_INPUT_CLASSES,
            'x-bind:type': "show ? 'text' : 'password'",
        }),
    )
    new_password2 = forms.CharField(
        label='تأكيد كلمة المرور الجديدة',
        widget=forms.PasswordInput(attrs={
            'class': PASSWORD_INPUT_CLASSES,
            'x-bind:type': "show ? 'text' : 'password'",
        }),
    )


DEFAULT_RETAIL_ACCOUNT_TYPE_NAME = 'قطاعي'


class RegisterForm(UserCreationForm):
    """
    فورم تسجيل B2C مبسّط (Phase 2): اسم، هاتف، إيميل اختياري، باسورد بس.
    مفيش business_name/account_type/address إجباريين هنا — دول بيتاخدولهم
    قيم افتراضية تلقائيًا في save() عشان الموديل (ClientProfile) يفضل
    زي ما هو من غير أي migration، بينما تجربة العميل بسيطة وسريعة.
    رقم الهاتف نفسه هو الـ username (العميل مش محتاج يفتكر يوزرنيم منفصل).
    """
    full_name = forms.CharField(max_length=150, label='الاسم')
    phone = forms.CharField(max_length=20, label='رقم الهاتف')
    email = forms.EmailField(label='البريد الإلكتروني (اختياري)', required=False)

    # بدون field_order، الحقول الموروثة من UserCreationForm (password1/2)
    # كانت هتظهر فوق full_name/phone/email في الفورم — بنفرض الترتيب
    # المنطقي (بيانات العميل الأول، الباسورد آخر حاجة) صراحةً هنا.
    field_order = ['full_name', 'phone', 'email', 'password1', 'password2']

    class Meta:
        model = User
        # username مش موجود هنا عمدًا — بيتحدد تلقائيًا من رقم الهاتف في
        # save() بدل ما نطلب من العميل يختار يوزرنيم منفصل.
        fields = ('password1', 'password2')

    def clean_phone(self):
        phone = self.cleaned_data['phone'].strip()
        # نفس رقم الهاتف بيبقى الـ username، فلازم يتفحص هنا (مش بس بعد
        # الحفظ) عشان رسالة الخطأ توضح للعميل إنه مسجّل قبل كده بدل ما
        # ياخد IntegrityError غامض من قاعدة البيانات.
        if User.objects.filter(username=phone).exists():
            raise forms.ValidationError('رقم الهاتف ده مسجّل بحساب من قبل. جرّب تسجّل دخول بدل كده.')
        return phone

    def save(self, commit=True):
        user = super().save(commit=False)
        phone = self.cleaned_data['phone']
        full_name = self.cleaned_data['full_name']
        user.username = phone
        user.first_name = full_name
        # الإيميل اختياري في الفورم، لكن الحقل في User فريد (unique) —
        # لو العميل سابه فاضي، بنولّد قيمة فريدة تلقائية بدل ما نسيبه
        # فاضي ويتعارض مع عميل تاني سايبه فاضي هو كمان.
        user.email = self.cleaned_data.get('email') or f'{phone}@no-email.mgpharmacy.local'
        user.role = User.Role.CLIENT
        # Phase 2: دخول فوري بدون موافقة أدمن — كل عميل جديد ACTIVE على طول.
        user.status = User.Status.ACTIVE
        if commit:
            user.save()
            # نوع الحساب "قطاعي" بقى بيتعمل رسميًا في data migration
            # (accounts/0010_create_retail_account_type). get_or_create هنا
            # فضل كـ fallback دفاعي بس (يشتغل حتى لو الـ migration بشكل ما
            # لسه ما اتطبقتش)، مش هو المصدر الرسمي لإنشاء النوع بقى.
            retail_type, _ = AccountType.objects.get_or_create(
                name=DEFAULT_RETAIL_ACCOUNT_TYPE_NAME,
                defaults={'default_unit_size': AccountType.UnitSize.SMALL},
            )
            ClientProfile.objects.create(
                user=user,
                business_name=full_name,
                account_type=retail_type,
                address='',
                phone=phone,
            )
        return user


class LoginForm(forms.Form):
    # Phase 2: رقم الهاتف بقى هو الـ username لأي عميل جديد، فبنوضّح ده في
    # اللابل (مع الإبقاء على "اسم المستخدم" بين قوسين لأي حساب قديم كان
    # موجود قبل Phase 2 بيوزرنيم تاني).
    username = forms.CharField(label='رقم الهاتف (أو اسم المستخدم)')
    password = forms.CharField(widget=forms.PasswordInput, label='كلمة المرور')
