# Task 2 — Field Guide

This is a document-to-record task. Shape the output for machines, not humans.

**Focus on:** reading the `json_schema` from the request and extracting every field it specifies. Information accuracy is 70% of the score — getting the right value matters most.

**Watch out for:** tables (common in financial and medical documents), handwritten or low-quality scans (~36% of eval set), nested objects and arrays, numbers that need to be extracted as the correct type (int vs float vs string).

**Good solutions tend to:** use a vision model with the JSON schema in the prompt, validate output against the schema before returning, handle tables by mapping rows to array items, and fail gracefully per-field (return null) instead of blowing up the whole extraction.
