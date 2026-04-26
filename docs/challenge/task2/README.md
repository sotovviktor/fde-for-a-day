# Task 2: Document Extraction

`POST /extract`

Given a document image (receipt, invoice, form, financial statement, etc.) and a JSON schema describing the expected output structure, extract all relevant data into structured JSON. The scoring cares about getting the right values with the right structure — not about how pretty your output reads.

Read the background:

- [customer_brief.md](customer_brief.md) — what the customer needs
- [field_guide.md](field_guide.md) — practical extraction tips
- [engineering_review.md](engineering_review.md) — what judges look for

## Request Contract

Input fields:

- `document_id`
- `content` — base64-encoded document image
- `content_format` — `"image_base64"`
- `json_schema` — JSON schema describing the expected output structure (each document has a different schema)

See [../../../py/data/task2/input_schema.json](../../../py/data/task2/input_schema.json) for the formal schema.

## Response Contract

Required output fields:

- `document_id` — must match the input
- All fields specified in the `json_schema` from the request

The output schema varies per document. One document might ask for `firstName`, `address`, `phone`. Another might ask for `tableData`, `institution`, `portfolioSummary`. Your endpoint must read the `json_schema` and return matching structured JSON.

See [../../../py/data/task2/output_schema.json](../../../py/data/task2/output_schema.json) for the formal schema.

## Resolution Scoring

```
resolution = (0.70 x information_accuracy + 0.30 x text_fidelity) x 100
```

| Dimension | Weight | Metric |
|---|---|---|
| `information_accuracy` | 70% | Recursive field F1 with value normalization — did you extract the correct data? |
| `text_fidelity` | 30% | Recursive field exact-match — did you preserve exact formatting? |

**Information Accuracy** uses a format-stripping normalizer: `$1,234.56` → `1234.56`, `10%` → `10`. If you extract the right value in a different format, you still get credit.

**Text Fidelity** uses a light normalizer (lowercase, collapse whitespace). If you also match the exact formatting, you get the full score.

**Per-field scoring by type:**
- Strings → token F1 (information) / exact match (fidelity)
- Numbers → 1% relative tolerance
- Booleans → exact match
- Lists → set F1 with fuzzy element alignment (information) / strict set F1 (fidelity)
- Nested objects → recursive field-mean

## What's Hard

Every document has a different schema — you can't hardcode field names. Receipts, invoices, medical forms, financial statements, charts. Some have tables, some have nested sections. ~36% of documents are adversarial (photographed, scanned, handwritten — degraded image quality).

## Tips

- Read the `json_schema` from the request — it tells you exactly what fields to extract.
- Use a vision model (GPT-4o, GPT-4.1, etc.) — the input is an image, not text.
- `information_accuracy` is 70% of the score — getting the right value matters more than matching format exactly.
- Return `null` for fields you can't extract — don't hallucinate.
- Tables are common. Financial data, medical forms, and invoices all have tabular content.
