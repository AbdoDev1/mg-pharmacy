# Phase 0 — نشر MG Pharmacy جنب Biozone على نفس السيرفر

هذا الملف تعليمات تشغيل فعلية على السيرفر (Debian PC بتاعك) — نفّذها بالترتيب.

## 0) قبل البدء — تأكد

- المجلد ده (`mg-pharmacy/`) **منفصل تمامًا** عن مجلد Biozone الأصلي — حطه
  بجنبه على نفس المستوى، مش جواه:
  ```
  /home/you/biozone/          ← الأصلي، شغال على 8080
  /home/you/mg-pharmacy/      ← الجديد، هيشتغل على 8081
  ```
- Biozone الأصلي **يفضل شغال زي ما هو من غير أي إيقاف** طول الوقت ده.

## 1) Git (اختياري لكن موصى به)

```bash
cd mg-pharmacy
git init
git add .
git commit -m "Initial fork from Biozone — MG Pharmacy Phase 0"
```

## 2) ملف البيئة

```bash
cp .env.production.example .env.production
nano .env.production
```
- غيّر `SECRET_KEY` لقيمة عشوائية جديدة (مختلفة عن Biozone تمامًا).
- غيّر `DB_PASSWORD` لباسورد مختلف عن Biozone.
- `DB_NAME`/`DB_USER` أصلًا `mgpharmacy` — سيبهم زي ما هما إلا لو عايز اسم تاني.

## 3) تشغيل الحاويات (بعلم `-p` صراحةً عشان العزل الكامل)

```bash
docker compose -p mgpharmacy up -d --build
```

هيشغّل: `db` (Postgres منفصل تمامًا)، `redis` (منفصل)، `web` (Django)،
`nginx` (على بورت host **8081**، مش 8080).

## 4) تأكيد إنشاء أدمن أول (اختياري، أو دخول shell)

```bash
docker compose -p mgpharmacy exec web python manage.py createsuperuser
```

## 5) اختبار العزل (الخطوة الأهم في Phase 0)

من متصفح على أي جهاز في نفس الشبكة (أو من السيرفر نفسه):

- `http://<server-ip>:8081/` → لازم يفتح نسخة MG Pharmacy (لسه بمظهر
  Biozone القديم دلوقتي — الشكل هيتغيّر في Phase 1).
- `http://<server-ip>:8080/` → لازم Biozone الأصلي **لسه شغال بالظبط زي
  الأول**، من غير أي بطء أو تأثير ملحوظ.

تأكد كمان إن:
```bash
docker ps
```
بيوريك containers منفصلة تمامًا لكل مشروع (أسماء زي `mgpharmacy-db-1`،
`mgpharmacy-web-1`... مختلفة عن `biozone-db-1` إلخ).

## 6) لاحقًا — Cloudflare Tunnel منفصل

لما تيجي تعرّض MG Pharmacy على الإنترنت، محتاج hostname/tunnel **منفصل**
عن Biozone (نفس مبدأ `cloudflared` اللي شغّال حاليًا، config تاني بيوجّه
لـ `localhost:8081` بدل `8080`). ده مش جزء من Phase 0 — نأجله لحد ما
تقرر الدومين.

---

## تشغيل لوكال بس (بدون أي دومين/Cloudflare) — مفيد للتجربة والتطوير

نفس خطوات 1-5 فوق بالظبط تشتغل محليًا من غير أي تعديل إضافي — الإعدادات
الافتراضية أصلًا بتغطي `localhost`/`127.0.0.1` على البورت `8081`
(`ALLOWED_HOSTS` و`CSRF_TRUSTED_ORIGINS` في `config/settings.py` معدّين
لبورت 8081 تحديدًا لهذا الفورك). يعني بعد:
```bash
docker compose -p mgpharmacy up -d --build
```
تقدر تفتح المتصفح على `http://localhost:8081/` مباشرة وتجرّب كل حاجة
(تصفح، تسجيل دخول، لوحة الموظفين على `/staff/`) من غير أي إعداد شبكة
إضافي. مفيش فرق في السلوك بين التشغيل اللوكال والتشغيل عبر Cloudflare —
الفرق الوحيد هو الوصول من برّه جهازك، وده بيتحدد لاحقًا في خطوة 6.

**ملحوظة:** لو غيّرت البورت في `docker-compose.yml` لأي رقم تاني غير
8081، لازم تحدّث `CSRF_TRUSTED_ORIGINS` في `config/settings.py` بنفس
الرقم الجديد (السطرين اللي فيهم `localhost:8081`/`127.0.0.1:8081`)،
وإلا أي فورم (تسجيل دخول/تسجيل حساب/checkout) هيترفض بخطأ CSRF.


---

بعد ما تتأكد إن التشغيل (لوكال أو على السيرفر) نجح والاتنين شغالين مع
بعض من غير مشاكل، رجّعلي تأكيد ("Phase 0 نجحت") وهكمل تحديث
`MG_PHARMACY_PLAN.md` وأبدأ Phase 1.
