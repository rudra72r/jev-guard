# PII redaction

```python
from jev_guard import redact

clean, found = redact("Mail jane@example.com or call 555-0147", level="fast")
# clean == "Mail [EMAIL] or call [PHONE]"
# found == [{"type": "email", "span": (5, 21), "value": "[EMAIL]"},
#           {"type": "phone", "span": (30, 38), "value": "[PHONE]"}]
```

`aredact()` is the async version. `found` never contains the original values, only what replaced them. Spans index into the original text.

## Two levels

| level | what it catches | cost | notes |
|---|---|---|---|
| `fast` | emails, phone numbers (US and international), US SSNs, payment card numbers | free, local, deterministic | Card numbers must pass the Luhn check, so order and tracking numbers survive. Misses names and addresses. |
| `strict` (default) | everything `fast` catches, plus sentences that still identify someone | one Jev call, ~$0.00002 per paragraph | Replaces whole sentences with `[REDACTED]`. |

## How strict mode works, and its tradeoff

1. The regex pass masks everything it recognises.
2. The text is split into sentences (dots inside emails, URLs, and numbers aren't sentence ends). Up to 32 chunks go to Jev in **one** call. Each chunk is asked: *does this still contain personal information that identifies a real person?* Already-masked `[EMAIL]`-style placeholders don't count.
3. Every sentence Jev flags (probability > 0.6) is replaced whole.

Jev classifies text; it doesn't point at words. So strict mode redacts **whole sentences**, which over-redacts rather than leaving a name next to a masked email. Only masked text is sent to Jev: raw emails, phone numbers, SSNs, and card numbers never leave your process.

## Limits

- `strict` is only as good as Jev's judgement of "identifies a real person". Evaluate it on your own data before relying on it.
- Formats outside the list above (IBANs, national IDs other than US SSNs, passport numbers) are only caught in `strict` mode, at sentence level.
- Redaction reduces exposure. It isn't a compliance certification.
