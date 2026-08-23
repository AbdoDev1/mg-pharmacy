"""
تنظيف ملفات الإكسل المؤقتة (تصدير/استيراد) اللي فضلت على القرص من غير مسح.

EXPORT_TMP_DIR: ملف التصدير بيتبني بواسطة build_products_export
(products/tasks.py) وبيفضل موجود على القرص لحد ما export_products_download
يقدّمه ويمسحه. حالة الكاش اللي بتشاور عليه (export_status_cache_key) ليها
TTL نص ساعة بس — لو الموظف بدأ تصدير وماحملوش، الكاش بيمسح نفسه لوحده لكن
الملف الفعلي مش بيتلمس، فبيفضل على القرص للأبد من غير الأمر ده.

IMPORT_TMP_DIR: أقل عرضة لنفس المشكلة عمليًا، لأن parse_import_file
(products/tasks.py) بتمسح الملف المرفوع في finally دايمًا (نجح أو فشل) —
لكن لو الـtask نفسها ماتنفذتش خالص (عطل في الطابور مثلًا)، الملف المرفوع
هيفضل هو كمان، فبنمسحه هنا كتغطية إضافية.

آمن يتنفذ أكتر من مرة، ومفروض يتحط في crontab دوري (كل ساعة مثلاً) زي
activity.trim_activity_logs و notifications.trim_notifications.

الاستخدام:
    python manage.py cleanup_export_files
    python manage.py cleanup_export_files --hours 2
"""
import time
from pathlib import Path

from django.core.management.base import BaseCommand

from staff.views.products.import_export import EXPORT_TMP_DIR, IMPORT_TMP_DIR

DEFAULT_MAX_AGE_HOURS = 1


class Command(BaseCommand):
    help = (
        f'يمسح ملفات .xlsx المؤقتة (تصدير/استيراد) الأقدم من '
        f'{DEFAULT_MAX_AGE_HOURS} ساعة افتراضيًا من EXPORT_TMP_DIR و '
        'IMPORT_TMP_DIR. آمن يتنفذ أكتر من مرة، ومفروض يتحط في crontab '
        'دوري (كل ساعة مثلاً) زي activity.trim_activity_logs.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=float,
            default=DEFAULT_MAX_AGE_HOURS,
            help=f'عمر الملف بالساعات قبل ما يتمسح (افتراضي {DEFAULT_MAX_AGE_HOURS}).',
        )

    def handle(self, *args, **options):
        max_age_seconds = options['hours'] * 3600
        cutoff = time.time() - max_age_seconds

        total_deleted = 0
        for directory in (EXPORT_TMP_DIR, IMPORT_TMP_DIR):
            total_deleted += self._cleanup_dir(directory, cutoff)

        self.stdout.write(self.style.SUCCESS(
            f'تم مسح {total_deleted} ملف أقدم من {options["hours"]} ساعة.'
        ))

    def _cleanup_dir(self, directory: Path, cutoff: float) -> int:
        if not directory.exists():
            return 0

        deleted_count = 0
        for path in directory.iterdir():
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    deleted_count += 1
            except OSError:
                # ملف اتمسح بالفعل من عملية تانية (مثلًا export_products_download
                # طلع نفس اللحظة) — تجاهل ومتابعة الباقي.
                continue
        return deleted_count
