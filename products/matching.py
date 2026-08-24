"""
أدوات مطابقة أسماء الأصناف — الهدف: منع تكرار الصنف في قاعدة البيانات
لمجرد اختلاف بسيط وشكلي في الاسم (مسافات زيادة، أرقام عربي/إنجليزي،
حروف متشابهة زي ا/أ/إ/آ أو ي/ى أو ه/ة)، مع ترك أي اختلاف حقيقي في الاسم
لمراجعة بشرية بدل ما يتم دمجه تلقائيًا.
"""
import re
from difflib import SequenceMatcher

from django.contrib.postgres.search import TrigramSimilarity

_ARABIC_INDIC_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')
_DIACRITICS_RE = re.compile(r'[\u0617-\u061A\u064B-\u0652\u0670\u06D6-\u06ED]')
_WHITESPACE_RE = re.compile(r'\s+')

# خرائط توحيد الحروف المتشابهة شكليًا واللي بتختلف كتابةً من شخص للتاني
# لنفس الكلمة (مش بتغيّر معنى الكلمة، بس بتوحّد طريقة كتابتها للمقارنة فقط
# — الاسم الأصلي name_ar بيفضل زي ما اتكتب بالظبط، التطبيع ده لغرض
# المطابقة الداخلية بس).
_CHAR_MAP = str.maketrans({
    'أ': 'ا', 'إ': 'ا', 'آ': 'ا', 'ٱ': 'ا',
    'ى': 'ي',
    'ة': 'ه',
    'ؤ': 'و',
    'ئ': 'ي',
})


def normalize_name(name: str) -> str:
    """
    تطبيع اسم الصنف لغرض المطابقة (مش للعرض): إزالة الفراغات الزيادة،
    توحيد الأرقام والحروف المتشابهة، وتحويل النص لحالة موحّدة.
    نفس الاسم بأشكال كتابة مختلفة هيرجع نفس الـ normalize_name بالظبط.
    """
    if not name:
        return ''
    text = str(name).strip()
    text = text.translate(_ARABIC_INDIC_DIGITS)
    text = _DIACRITICS_RE.sub('', text)
    text = text.translate(_CHAR_MAP)
    text = _WHITESPACE_RE.sub(' ', text)
    return text.strip().lower()


def similarity(a: str, b: str) -> float:
    """نسبة تشابه (0-1) بين نصّين مُطبَّعين مسبقًا."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_similar_products(name_key, candidates, threshold=0.82, limit=3):
    """
    بيدوّر بين قائمة منتجات (candidates — أي iterable فيه .name_key و .pk)
    عن أقرب الأسماء لـ name_key، ويرجّع أفضل `limit` نتيجة بنسبة تشابه
    >= threshold، مرتبة من الأعلى تشابهًا. مش بيدمج تلقائي — بس بيقترح.

    فلترة أولية رخيصة قبل SequenceMatcher (المكلّف): نسبة SequenceMatcher.ratio
    محكومة رياضيًا بأطوال النصين (ratio <= 2*min(len)/(len_a+len_b))، فلو
    طول اسمين مختلف جدًا عن بعض، مستحيل يوصلوا threshold حتى لو كل حرف
    فيهم متطابق. تجاهل المرشحين دول بدون حتى استدعاء similarity() بيقلل
    عدد المقارنات الفعلية بشكل كبير مع كتالوجات كبيرة — من غير ما يغيّر
    النتيجة النهائية خالص (نفس المرشحين اللي كانوا هيرجعوا زي ما هم).
    """
    if not name_key:
        return []
    name_len = len(name_key)
    scored = []
    for product in candidates:
        other_key = product.name_key
        if not other_key:
            continue
        other_len = len(other_key)
        max_possible_ratio = 2 * min(name_len, other_len) / (name_len + other_len)
        if max_possible_ratio < threshold:
            continue
        ratio = similarity(name_key, other_key)
        if ratio >= threshold:
            scored.append((product, round(ratio * 100)))
    scored.sort(key=lambda item: -item[1])
    return scored[:limit]


def find_similar_products_db(name_key, threshold, limit=3, queryset=None):
    """
    نفس فكرة find_similar_products (اقتراح صنف مشابه للمراجعة، مش دمج
    تلقائي)، لكن بيستعلم قاعدة البيانات مباشرة بـtrigram similarity
    (pg_trgm — مفعّلة في migration 0019، وname_key عليه GIN index
    بـgin_trgm_ops بالفعل في products/models.py) بدل ما يجيب الكتالوج
    كله لذاكرة Python ويقارن كل صف بكل الكتالوج بـSequenceMatcher. الشكل
    القديم كان O(rows × catalog) فعليًا وهو سبب مشاكل الأداء/الـtimeout
    مع كتالوجات كبيرة أثناء الاستيراد.

    مهم جدًا: trigram similarity مقياس مختلف جوهريًا عن
    SequenceMatcher.ratio القديم (نفس القيم مش قابلة للمقارنة المباشرة) —
    threshold هنا لازم يتحدد بمعايرة فعلية على بيانات حقيقية، مش بنفس
    رقم threshold القديم (0.82). راجع
    scripts/tests/calibrate_trigram_threshold.py، وشوف
    common.FUZZY_MATCH_THRESHOLD_TRIGRAM (القيمة الحالية مبدئية لحد ما
    تشغّل السكريبت على بيانات إنتاج حقيقية وتحدّثها).

    بيرجّع نفس شكل النتيجة القديم بالظبط: قائمة (product, score) مرتبة
    تنازليًا، score = trigram similarity من 0 لـ100 (بدل نسبة
    SequenceMatcher). queryset اختياري — الافتراضي كل المنتجات (زي
    السلوك القديم اللي كان بيقارن حتى مع منتجات غير نشطة)؛ لو محتاج
    تستثني صنف معيّن (مثلًا وقت التعديل) مرّر queryset.exclude(pk=...).
    """
    if not name_key:
        return []
    from products.models import Product
    qs = queryset if queryset is not None else Product.objects.all()
    results = list(
        qs.annotate(_similarity=TrigramSimilarity('name_key', name_key))
        .filter(_similarity__gte=threshold)
        .order_by('-_similarity')[:limit]
    )
    return [(p, round(p._similarity * 100)) for p in results]
