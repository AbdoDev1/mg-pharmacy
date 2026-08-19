#!/usr/bin/env python3
"""
فحص ثابت (static check) — مش unit test بالمعنى التقليدي، بيشتغل في ثواني
بدون DB أو أي اعتماديات خارج المكتبة القياسية (عمدًا: عشان يبقى أول خطوة
سريعة في CI، قبل حتى تثبيت باقي المتطلبات لو حبينا مستقبلًا).

بيمنع تكرار مشكلة biozone بالضبط: بعد تقسيم خدمة "web" الواحدة لـ
web-store/web-staff/celery-worker، فضل فيه سكريبتات بتنادي على اسم
الخدمة القديمة "web" اللي مبقاش موجود، وده فشل صامت (exit code مش صفر
بس محدش كان بيراجعه) اتكشف يدويًا بعد أسابيع.

الفكرة: نستخرج أسماء الخدمات الحقيقية من docker-compose.yml (top-level
keys تحت `services:`)، وبعدين نـ grep كل ملفات .sh/.py في المشروع بحثًا
عن استدعاءات `docker compose exec/stop/start/restart` ونتأكد إن كل اسم
خدمة مذكور فيها موجود فعليًا في القائمة دي.

راجع mg-pharmacy-testing-strategy.md (قسم "سكريبتات Shell + docker-compose")
لتفاصيل القرار.
"""
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
COMPOSE_FILE = PROJECT_DIR / 'docker-compose.yml'

# ملفات بيتفحصوا (نفس مجلد السكريبت ده مستثنى — مفيش داعي يفحص نفسه).
SCAN_GLOBS = ['**/*.sh', '**/*.py']
EXCLUDE_DIRS = {'.git', 'node_modules', 'venv', '.venv', 'migrations'}

# "docker compose <subcommand> [flags/options] <service...>"
COMPOSE_CALL_RE = re.compile(r'docker\s+compose\s+(exec|stop|start|restart|logs|run)\b(.*)$')

# فلاجات معروفة بتاخد قيمة بعدها لازم تتجاهل هي والقيمة اللي بعدها
# (زي -e PGPASSWORD=... أو --env FOO=bar) — لو فلاج غير معروف اتضاف
# بعدين ومحتاج قيمة، أضيفه هنا.
FLAGS_WITH_VALUE = {'-e', '--env'}
# subcommands بتاخد اسم خدمة واحد بس (باقي السطر بعده أمر يتنفذ، مش
# أسماء خدمات إضافية) — عكس stop/start/restart اللي ممكن ياخدوا أكتر من
# اسم خدمة في نفس السطر.
SINGLE_SERVICE_SUBCOMMANDS = {'exec', 'run', 'logs'}


def extract_compose_service_names(compose_text):
    lines = compose_text.splitlines()
    services = set()
    in_services_block = False
    for line in lines:
        if re.match(r'^services:\s*$', line):
            in_services_block = True
            continue
        if in_services_block:
            if re.match(r'^\S', line):  # رجعنا لعمود 0 — خلصنا قسم services
                break
            match = re.match(r'^  ([A-Za-z0-9_.-]+):', line)
            if match:
                services.add(match.group(1))
    return services


# اسم خدمة حقيقي: حروف/أرقام/شرطة/نقطة بس، وفيه حرف/رقم واحد على الأقل
# (يستثني "..." اللي بتظهر في التعليقات/التوثيق كـ placeholder). أي توكن
# مايطابقش الشكل ده بالكامل (زي "web-store)" أو ")" جوه تعليق عربي) معناه
# إن السطر ده مش استدعاء حقيقي (غالبًا تعليق أو docstring بيتكلم عن الأمر)
# — بنسيبه من غير ما نعتبره violation، عشان الفحص ده مخصص للاستدعاءات
# الفعلية بس مش لتحليل نص حر.
_SERVICE_TOKEN_RE = re.compile(r'^(?=.*[A-Za-z0-9])[A-Za-z0-9_.-]+$')


def extract_service_references(line, subcommand, rest):
    tokens = rest.split()
    services = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in FLAGS_WITH_VALUE:
            i += 2
            continue
        if token.startswith('-'):
            i += 1
            continue
        if not _SERVICE_TOKEN_RE.match(token):
            # مش توكن اسم خدمة نضيف — على الأرجح السطر ده تعليق/توثيق حر
            # مش استدعاء فعلي. نوقف من غير ما نسجّل أي حاجة من السطر ده.
            return []
        services.append(token)
        if subcommand in SINGLE_SERVICE_SUBCOMMANDS:
            break
        i += 1
    return services


def iter_scanned_files():
    for pattern in SCAN_GLOBS:
        for path in PROJECT_DIR.glob(pattern):
            if path.resolve() == Path(__file__).resolve():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            yield path


def main():
    if not COMPOSE_FILE.exists():
        print(f'خطأ: {COMPOSE_FILE} مش موجود.')
        return 1

    known_services = extract_compose_service_names(COMPOSE_FILE.read_text(encoding='utf-8'))
    if not known_services:
        print('خطأ: مقدرش أستخرج أي اسم خدمة من docker-compose.yml — راجع صيغة الملف.')
        return 1

    violations = []
    for path in iter_scanned_files():
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        for line_num, line in enumerate(text.splitlines(), start=1):
            match = COMPOSE_CALL_RE.search(line)
            if not match:
                continue
            subcommand, rest = match.group(1), match.group(2)
            for service in extract_service_references(line, subcommand, rest):
                if service not in known_services:
                    violations.append(
                        f'{path.relative_to(PROJECT_DIR)}:{line_num}: اسم خدمة غير موجود '
                        f'في docker-compose.yml: "{service}"\n    {line.strip()}'
                    )

    if violations:
        print('فشل الفحص — استدعاءات docker compose بتشاور على أسماء خدمات مش موجودة:\n')
        for v in violations:
            print(f'  - {v}')
        print(f'\nأسماء الخدمات الفعلية في docker-compose.yml: {", ".join(sorted(known_services))}')
        return 1

    print(f'تمام — كل استدعاءات docker compose بتشاور على خدمات موجودة فعليًا ({len(known_services)} خدمة).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
