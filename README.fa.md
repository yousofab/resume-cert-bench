<div dir="rtl">

# گواه ازسرگیری در گردش کار عامل‌های هوش مصنوعی

[نسخه‌ی انگلیسی](README.md) · [پروپوزال PDF](docs/proposal/ResumeCert_Research_Proposal_FA.pdf) · [پروپوزال Word](docs/proposal/ResumeCert_Research_Proposal_FA.docx) · [قدردانی علمی](docs/ACKNOWLEDGMENTS.md)

وقتی عامل پس از وقفه به گردش کار برمی‌گردد، چه چیزی را باید دوباره بخواند تا بتواند اعتبار قدم بعدی را با بودجه‌ی محدود اثبات کند؟ برای نمونه، تایید قبلی سندی که نسخه‌اش تغییر کرده، لزوما مجوز انتشار نسخه‌ی جاری نیست. گواه ازسرگیری، تصمیم ادامه یا رد را به مشاهده‌های قابل بازبینی وصل می‌کند؛ اگر هر دو تصمیم هنوز ممکن باشند، عامل تصمیم را به تعویق می‌اندازد.

**وضعیت پژوهش:** پروپوزال رسمی و مثال صوری هشت‌حالته آماده‌اند. در مثال، با دو خواندن، بهترین مجموعه‌ی ثابت در ۴ حالت از ۸ و روش تطبیقی در ۸ حالت از ۸ به گواه می‌رسد. این نتیجه به همان مثال محدود است. شبیه‌ساز و داور مستقل هر ۶۴ اجرای مثال را بازتولید کردند. پایلوت توسعه با ۲۴ جفت و ۱۲ کنترل جداگانه انجام شد. با بودجه‌ی دو خواندن، روش تطبیقی ساده در ۲۲ مورد از ۴۸ و بهترین ترتیب ثابت در ۱۹ مورد از ۴۸ گواه گرفت. با خواندن گروهی ارزان، هر دو به ۳۲ مورد رسیدند. پروتکل آزمون تأییدی، شامل ۴۸ جفت در دو خانواده‌ی تازه، پیش از اجرا ثبت شده است. در آزمون تأییدی، هر دو روش با بودجهٔ دو خواندن در همان **۱۱ وضعیت از ۹۶ وضعیت** گواه گرفتند و هر کدام ۱۸۴ واحد هزینه کردند. نتیجهٔ ثبت‌شده «بدون مزیت در مجموعهٔ ترکیبی» است. با افزودن خواندن گروهیِ یک‌واحدی، هر دو با همان هزینهٔ کل به ۳۰ گواه رسیدند.

## مطالعهٔ انتقال به SQLite

در یک گردش کار آرشیو، بین صدور گواه و ثبت اقدام وضعیت را تغییر دادیم. در بودجهٔ چهار، بازاعتبارسنجی شواهد کافی ۶۴ رسید مجاز و صفر رسید نامجاز ثبت کرد. تطبیق همهٔ شواهد ۶۰ و ارزیابی کامل پیش‌شرط ۷۶ رسید مجاز ثبت کردند. [طراحی آزمایش، نتیجه و ردپاها](docs/research/ARCHIVE_TRANSFER_V1_FA.md)

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
```
