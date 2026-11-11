<div dir="rtl">

# گواه ازسرگیری در گردش کار عامل‌های هوش مصنوعی

[نسخه‌ی انگلیسی](README.md) · [پروپوزال PDF](docs/proposal/ResumeCert_Research_Proposal_FA.pdf) · [پروپوزال Word](docs/proposal/ResumeCert_Research_Proposal_FA.docx) · [قدردانی علمی](docs/ACKNOWLEDGMENTS.md)

وقتی عامل پس از وقفه به گردش کار برمی‌گردد، چه چیزی را باید دوباره بخواند تا بتواند اعتبار قدم بعدی را با بودجه‌ی محدود اثبات کند؟ برای نمونه، تایید قبلی سندی که نسخه‌اش تغییر کرده، لزوما مجوز انتشار نسخه‌ی جاری نیست. گواه ازسرگیری، تصمیم ادامه یا رد را به مشاهده‌های قابل بازبینی وصل می‌کند؛ اگر هر دو تصمیم هنوز ممکن باشند، عامل تصمیم را به تعویق می‌اندازد.

**وضعیت پژوهش:** پروپوزال رسمی و مثال صوری هشت‌حالته آماده‌اند. در مثال، با دو خواندن، بهترین مجموعه‌ی ثابت در ۴ حالت از ۸ و روش تطبیقی در ۸ حالت از ۸ به گواه می‌رسد. این نتیجه به همان مثال محدود است. شبیه‌ساز و داور مستقل هر ۶۴ اجرای مثال را بازتولید کردند. پایلوت توسعه با ۲۴ جفت و ۱۲ کنترل جداگانه انجام شد. با بودجه‌ی دو خواندن، روش تطبیقی ساده در ۲۲ مورد از ۴۸ و بهترین ترتیب ثابت در ۱۹ مورد از ۴۸ گواه گرفت. با خواندن گروهی ارزان، هر دو به ۳۲ مورد رسیدند. آزمون تاییدی روی خانواده‌های کنارگذاشته‌شده هنوز اجرا نشده است.

## منابع و بازتولید

- [طرح پژوهش](docs/research/RESEARCH_PROTOCOL_FA.md)
- [ماتریس منابع دست‌اول](docs/research/EVIDENCE_MATRIX_FA.md)
- [کد مثال](proof/witness.py) و [خروجی ثبت‌شده](proof/witness_result.json)
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
```
