#!/usr/bin/env python3
"""
سكريبت مؤقت لمعايرة FUZZY_MATCH_THRESHOLD_TRIGRAM (products/services/import_export/common.py).

الهدف: مقياس trigram similarity (pg_trgm) مختلف جوهريًا عن SequenceMatcher.ratio
القديم (شوف التعليق في common.py)، فمينفعش نستخدم نفس رقم الـ0.82 القديم مباشرة —
لازم نجرّب threshold على عينة أزواج أسماء معروف مسبقًا هل المفروض تتطابق (نفس
الصنف بفروق كتابة حقيقية بعد التطبيع) ولا لأ (أصناف مختلفة فعلًا)، ونشوف أي
threshold بيدّي أقل عدد false positives/negatives.

ملاحظة مهمة عن اختيار الأزواج: normalize_name() (في products/matching.py) بيوحّد
الهمزات/التاء المربوطة/الأرقام/المسافات الزيادة تلقائيًا، ومطابقة الاسم التامة
(existing_by_name_key) بتمسك الحالة دي قبل ما توصل لمرحلة الـfuzzy أصلًا. يعني
الأزواج المفيدة للمعايرة هنا هي التشابه اللي بيفضل موجود *بعد* التطبيع (غلطة
إملائية حقيقية، حرف ناقص/زيادة، ترتيب كلمات) — مش الفروق اللي normalize_name
بيحلها لوحده.

بيحتاج اتصال بـPostgres فيه pg_trgm مفعّلة (نفس الـextension المستخدمة في
الإنتاج). الاتصال بياخد إعداداته من متغيرات البيئة القياسية لـpsycopg2
(PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD) — عدّلها حسب بيئتك المحلية.

الاستخدام:
    python3 scripts/tests/calibrate_trigram_threshold.py
"""
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path

import psycopg2

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR))

# استيراد normalize_name مباشرة من الكود الفعلي (مش نسخة مكررة هنا) —
# عشان لو التطبيع اتغيّر مستقبلًا، السكريبت ده يفضل بيعاير على نفس القيم
# الحقيقية اللي هتتخزن في name_key وقت الإنتاج. الاستيراد ده آمن من غير
# django.setup() لأن matching.py مبيلمسش الـmodels على مستوى الملف (بس
# جوه الدوال اللي مش هنستخدمها هنا).
from products.matching import normalize_name  # noqa: E402

# أزواج معروف مسبقًا هل نفس الصنف (بفرق كتابة حقيقي يفضل بعد normalize_name)
# ولا صنف مختلف فعلًا — مبنية على أنماط حقيقية شائعة في كتالوج صيدلية:
# غلطة إملائية بسيطة، حرف ناقص/زيادة، ترتيب كلمات — مقابل فروق جرعة/تركيز/
# شكل صيدلاني اللي المفروض تفضل أصناف منفصلة حتى لو الاسم شبه بعضه جدًا.
DUPLICATE_PAIRS = [
    ('بندول اكسترا', 'بنادول اكسترا'),                       # حرف ناقص (ا)
    ('كريم فيوسدين', 'كريم فيوسيدين'),                       # حرف ناقص (ي)
    ('امبيسلين 500', 'امبسيلين 500'),                        # حروف متبادلة
    ('شامبو كلير مضاد للقشرة', 'شامبو كلير مضاد القشرة'),     # فرق أداة تعريف
    ('مرهم عين تتراسيكلين', 'مرهم عين تتراسايكلين'),          # حرف زيادة (ا)
]

DIFFERENT_PAIRS = [
    ('بنادول اكسترا', 'بنادول كولد اند فلو'),                 # نفس البراند، صنف مختلف
    ('اوجمنتين 1 جم', 'اوجمنتين 625 مجم'),                    # تركيز مختلف — خطر false positive حقيقي
    ('شاش طبي معقم', 'قطن طبي معقم'),                         # نوع منتج مختلف
    ('فيتامين سي 1000 مجم', 'فيتامين د 1000 وحدة'),          # صنف مختلف تمامًا
    ('كريم فيوسيدين', 'كريم فيوسيدين اتش'),                   # تركيبة مختلفة (كلمة إضافية جوهرية)
    ('بندول اطفال شراب', 'بندول اقراص'),                      # شكل صيدلاني مختلف
]

CANDIDATE_THRESHOLDS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]


def get_trigram_similarity(cursor, text_a, text_b):
    cursor.execute('SELECT similarity(%s, %s)', [text_a, text_b])
    return cursor.fetchone()[0]


def main():
    conn = psycopg2.connect(
        host=os.environ.get('PGHOST', 'localhost'),
        port=os.environ.get('PGPORT', '5432'),
        dbname=os.environ.get('PGDATABASE', 'mg_calibrate'),
        user=os.environ.get('PGUSER', 'postgres'),
        password=os.environ.get('PGPASSWORD', 'postgres'),
    )
    cursor = conn.cursor()
    cursor.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')

    rows = []  # (name_a, name_b, is_duplicate_expected, old_ratio, trigram_sim)
    for name_a, name_b in DUPLICATE_PAIRS:
        key_a, key_b = normalize_name(name_a), normalize_name(name_b)
        old_ratio = SequenceMatcher(None, key_a, key_b).ratio()
        trigram_sim = get_trigram_similarity(cursor, key_a, key_b)
        rows.append((name_a, name_b, True, old_ratio, trigram_sim))

    for name_a, name_b in DIFFERENT_PAIRS:
        key_a, key_b = normalize_name(name_a), normalize_name(name_b)
        old_ratio = SequenceMatcher(None, key_a, key_b).ratio()
        trigram_sim = get_trigram_similarity(cursor, key_a, key_b)
        rows.append((name_a, name_b, False, old_ratio, trigram_sim))

    conn.close()

    print(f'{"صنف أ":<28}{"صنف ب":<30}{"متوقع":<8}{"SequenceMatcher":<18}{"trigram":<10}')
    for name_a, name_b, expected, old_ratio, trigram_sim in rows:
        print(
            f'{name_a:<28}{name_b:<30}'
            f'{"مطابق" if expected else "مختلف":<8}'
            f'{old_ratio:<18.3f}{trigram_sim:<10.3f}'
        )

    print('\nنتيجة تجربة كل threshold مرشّح على trigram similarity:\n')
    print(f'{"threshold":<12}{"false negatives":<18}{"false positives":<18}')
    best_threshold = None
    best_score = None
    for threshold in CANDIDATE_THRESHOLDS:
        false_negatives = sum(
            1 for _, _, expected, _, sim in rows if expected and sim < threshold
        )
        false_positives = sum(
            1 for _, _, expected, _, sim in rows if not expected and sim >= threshold
        )
        print(f'{threshold:<12}{false_negatives:<18}{false_positives:<18}')
        # الأولوية لتصفير false negatives (تفويت تكرار حقيقي = صنف مكرر
        # يتسجل من غير حتى اقتراح مراجعة، أسوأ من اقتراح زيادة يقدر
        # الموظف يتجاهله بضغطة واحدة)، وبعدين أقل false positives ممكنة.
        score = (false_negatives, false_positives)
        if best_score is None or score < best_score:
            best_score = score
            best_threshold = threshold

    print(
        f'\nالتوصية: FUZZY_MATCH_THRESHOLD_TRIGRAM = {best_threshold} '
        f'(false negatives={best_score[0]}, false positives={best_score[1]} '
        f'على عينة الاختبار دي — عيّنة صغيرة توضيحية، يُستحسن توسيعها بأسماء '
        f'حقيقية من كتالوج الإنتاج قبل اعتماد نهائي في بيئة حقيقية أكبر).'
    )


if __name__ == '__main__':
    main()
