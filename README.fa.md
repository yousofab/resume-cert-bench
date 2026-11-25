<div dir="rtl">

# گواه ازسرگیری در گردش کار عامل‌های هوش مصنوعی

[نسخه‌ی انگلیسی](README.md) · [پروپوزال PDF](docs/proposal/ResumeCert_Research_Proposal_FA.pdf) · [پروپوزال Word](docs/proposal/ResumeCert_Research_Proposal_FA.docx) · [قدردانی علمی](docs/ACKNOWLEDGMENTS.md)

وقتی عامل پس از وقفه به گردش کار برمی‌گردد، چه چیزی را باید دوباره بخواند تا بتواند اعتبار قدم بعدی را با بودجه‌ی محدود اثبات کند؟ برای نمونه، تایید قبلی سندی که نسخه‌اش تغییر کرده، لزوما مجوز انتشار نسخه‌ی جاری نیست. گواه ازسرگیری، تصمیم ادامه یا رد را به مشاهده‌های قابل بازبینی وصل می‌کند؛ اگر هر دو تصمیم هنوز ممکن باشند، عامل تصمیم را به تعویق می‌اندازد.

**وضعیت پژوهش:** پروپوزال رسمی و مثال صوری هشت‌حالته آماده‌اند. در مثال، با دو خواندن، بهترین مجموعه‌ی ثابت در ۴ حالت از ۸ و روش تطبیقی در ۸ حالت از ۸ به گواه می‌رسد. این نتیجه به همان مثال محدود است. شبیه‌ساز و داور مستقل هر ۶۴ اجرای مثال را بازتولید کردند. پایلوت توسعه با ۲۴ جفت و ۱۲ کنترل جداگانه انجام شد. با بودجه‌ی دو خواندن، روش تطبیقی ساده در ۲۲ مورد از ۴۸ و بهترین ترتیب ثابت در ۱۹ مورد از ۴۸ گواه گرفت. با خواندن گروهی ارزان، هر دو به ۳۲ مورد رسیدند. پروتکل آزمون تأییدی، شامل ۴۸ جفت در دو خانواده‌ی تازه، پیش از اجرا ثبت شده است. در آزمون تأییدی، هر دو روش با بودجهٔ دو خواندن در همان **۱۱ وضعیت از ۹۶ وضعیت** گواه گرفتند و هر کدام ۱۸۴ واحد هزینه کردند. نتیجهٔ ثبت‌شده «بدون مزیت در مجموعهٔ ترکیبی» است. با افزودن خواندن گروهیِ یک‌واحدی، هر دو با همان هزینهٔ کل به ۳۰ گواه رسیدند.

## مطالعهٔ انتقال به SQLite

در یک گردش کار آرشیو، بین صدور گواه و ثبت اقدام وضعیت را تغییر دادیم. در بودجهٔ چهار، بازاعتبارسنجی شواهد کافی ۶۴ رسید مجاز و صفر رسید نامجاز ثبت کرد. تطبیق همهٔ شواهد ۶۰ و ارزیابی کامل پیش‌شرط ۷۶ رسید مجاز ثبت کردند. [طراحی آزمایش، نتیجه و ردپاها](docs/research/ARCHIVE_TRANSFER_V1_FA.md)

## ترمیم گواه پس از وقفه

در مطالعهٔ اکتشافی SQLite، با حفظ شواهدی که نسخهٔ فیلدشان معتبر ماند، سقف سه خواندن تازه برای ثبت هر ۷۶ اقدام مجاز کافی بود. شروع از صفر در همان بودجه ۶۴ رسید ثبت کرد. [گزارش فارسی و مقایسهٔ هزینهٔ بررسی نسخه](docs/research/ARCHIVE_REPAIR_V1_FA.md)

## بازیابی ارسال به مقصد

نمونهٔ SQLite ثبت آرشیو را به صف پایدار و رسید یکتای مقصد وصل می‌کند. با دو فرصت بازیابی، روش کلید ثابت در هفت سناریو دقیقاً یک اثر ثبت کرد و قطعی ممتد در انتظار ماند. [گزارش فارسی](docs/research/ARCHIVE_DELIVERY_V1_FA.md)

## لغو مجوز در مقصد

در ۵۶ اجرای آزمایشی، کنترل اتمی نسل مجوز در SQLite جلوی ثبت تحت مجوز بسته یا قدیمی را گرفت. مسیر مستقل بازیابی رسید نیز نتیجهٔ اثرهای پیشین را پس از لغو گزارش داد. [گزارش فارسی](docs/research/DESTINATION_REVOCATION_V1_FA.md)

## انتقال به مقصد جایگزین

در ۶۴ اجرای آزمایشی، انتقال پس از گواه نبود اثر و بسته‌شدن دائمی عملیات در مقصد اول، هیچ ثبت تکراری بین دو مقصد ایجاد نکرد. [گزارش فارسی و ردپاها](docs/research/PROVIDER_FAILOVER_V1_FA.md)

## پاک‌سازی سوابق و بازیابی نسخهٔ قدیمی

در ۵۴ اجرا، مرز مستقلِ بسته‌شدن پیشوند جلوی بازاجرای عملیات بسته را پس از حذف یا بازیابی سوابق گرفت. کنترل مرزی، خطر باقی‌مانده برای عملیات هنوز باز را نشان داد. [گزارش فارسی و حدود نتیجه](docs/research/RETENTION_V1_FA.md)

## بازیابی عملیات هنوز باز

در ۶۳ اجرا، ثبت مستقل تلاش پیش از اثر، جلوی تکرار همان عملیات در همان مقصد را پس از از دست‌رفتن تصویر بازیابی گرفت. هشت وضعیت نامعلوم و کنترل مرزیِ بازگردانی دفتر مستقل نیز گزارش شده‌اند. [گزارش فارسی و هزینهٔ توقف کار](docs/research/OPEN_TAIL_V1_FA.md)

## حل وضعیت نامعلوم با شاهد مقصد

در مطالعهٔ ۶۰اجرایی، کنترلِ نوع و اتصال شاهد به عملیات، نتیجهٔ مثبت یا انتقال پس از بستن دائمیِ بدون اثر را می‌پذیرد. کنترل مرزی نشان می‌دهد بازگردانی خود مقصد، اعتبار عملیِ گواه قبلی را از بین می‌برد. [گزارش فارسی و حدود قرارداد](docs/research/TERMINAL_EVIDENCE_V1_FA.md)

## حفظ مرجع مقصد هنگام بازیابی

در [مطالعهٔ ۵۱ اجرایی](docs/research/PROVIDER_AUTHORITY_V1_FA.md)، ثبت اثر و بسته‌شدن عملیات بیرون از تصویر کاری نگه داشته شدند. بازگردانی خود مرجع، کنترل مرزی آزمایش بود.

## تشخیص نسخهٔ قدیمی مرجع

در [مطالعهٔ ۳۹ اجرایی](docs/research/AUTHORITY_ROLLBACK_V1_FA.md)، شاهد مستقل به کمک شمارهٔ نسخه و چکیدهٔ وضعیت، تصویرهای قدیمی یا هم‌شمارهٔ دارای محتوای متفاوت را رد می‌کند. خرابی بین دو ثبت و بازگردانی هم‌زمان شاهد نیز آزموده شده‌اند.

## منابع و بازتولید

- [طرح پژوهش](docs/research/RESEARCH_PROTOCOL_FA.md)
- [ماتریس منابع دست‌اول](docs/research/EVIDENCE_MATRIX_FA.md)
- [کد مثال](proof/witness.py) و [خروجی ثبت‌شده](proof/witness_result.json)
- [نتیجه و بازتولید آزمون تأییدی](docs/research/CONFIRMATORY_RESULTS_V1_FA.md)
- [پروتکل ثابت آزمون تأییدی](docs/research/CONFIRMATORY_PROTOCOL_V1_FA.md)
- [پایلوت توسعه و تحلیل نتیجه‌ها](docs/research/MATCHED_PILOT_V1_FA.md)
- [مرور منابع نوامبر ۲۰۲۶](docs/research/FRONTIER_2026_11.md)
- [مستند شبیه‌ساز و نتیجه‌ها](docs/research/SIMULATOR_V1_FA.md)
- [نقشه‌ی اجرا و حدود ادعا](docs/research/STATUS.md)

برای بازتولید و کنترل فایل‌ها، از ریشه‌ی مخزن با Python 3.12 دستور زیر را اجرا کنید:

</div>

```bash
python scripts/verify_repository.py
python -m unittest discover -s tests -v
python -m resumecert demo --check results/simulator_smoke_v1.json
python -m resumecert pilot --check results/matched_pilot_v1.json
python scripts/verify_freeze.py
python scripts/run_confirmatory.py --check results
python scripts/run_archive_transfer.py --check results
python scripts/run_archive_repair.py --check results
python scripts/run_archive_delivery.py --check results/archive_delivery_v1.json
python scripts/run_destination_revocation.py --check results/destination_revocation_v1.json
python scripts/run_provider_failover.py --check results/provider_failover_v1.json
python scripts/run_retention.py --check results/retention_v1.json
python scripts/run_open_tail.py --check results/open_tail_v1.json
python scripts/run_terminal_evidence.py --check results/terminal_evidence_v1.json
python scripts/run_provider_authority.py --check results/provider_authority_v1.json
python scripts/run_authority_rollback.py --check results/authority_rollback_v1.json
```
