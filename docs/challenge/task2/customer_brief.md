# Task 2 — Customer Brief

The customer has document images — receipts, invoices, financial statements, medical forms — that are only useful once they're structured data. Manual extraction is slow, inconsistent, and doesn't scale.

They want an API: send an image and a target schema, get back stable JSON. The fields vary per document type, but the quality bar is the same — accurate extraction with consistent formatting.

## What Breaks Trust

- Missing fields that are clearly visible in the image
- Hallucinated values the document doesn't support
- Inconsistent formatting (returning `"$1,234"` for one field and `"1234"` for another)
- Clean digital scans work fine but photographed or scanned documents fall apart
