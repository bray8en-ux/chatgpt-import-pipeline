# Privacy and Data Handling

This project is designed for local processing of personal ChatGPT exports.

## Rules

- Keep the original export outside the repository whenever possible.
- Do not commit real exports, generated candidate files, review results, or logs.
- Generated reports may contain conversation titles, source references, message hashes, and local filenames. Treat the whole output directory as private.
- The optional review bridge binds to `127.0.0.1` and only serves the review page and review result. Do not expose it through a proxy or public network.
- The bridge validates the candidate manifest and candidate-file hashes before accepting a result. This prevents accidentally applying a result to a different local run; it is not a replacement for human review.
- This project never deletes files, moves originals, or writes a formal knowledge base.

## Before publishing an example

Use synthetic data only. Remove personal names, titles, paths, message content, IDs, timestamps, and hashes unless they are deliberately fabricated for a test.
