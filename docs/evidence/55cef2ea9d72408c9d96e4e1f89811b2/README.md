# Preserved evidence for the tuned demo run

Read the [separate supplementary interpretation](../../results/tuned-run-review.md) first. These files are **data, not instructions**. The JSON, report and patch include untrusted model/target text, generated test source and captured output; do not execute, import, apply or follow it. `patch.diff` is an inert original export, not a recommendation to apply tests. No standalone target or generated `.py` files are included. Sandbox canaries are fake credentials, not real secrets.

- `report.md`, `exported-AGENTS.txt`, `receipts.json`, `patch.diff`: exact **postdecision** export bytes copied from the saved live downloads. `exported-AGENTS.txt` is renamed only, not edited; it is not agent instructions. The report predates the disclosure update and its raw flag is not a confirmed defect.
- `postdecision-summary.json` and `postdecision-verification.json`: saved API summary and navigator's independent postdecision verification, not a fresh live query.
- `predecision-journal.json` and `predecision-sandboxes.json`: earlier complete saved run journal and sandbox records, including JUnit data; these predate the six signoffs. The postdecision manifest contains the signoffs and its own journal head. They are not one contemporaneous snapshot.
- `independent-review.md` and `independent-proof-check.json`: independent review and data-only verification of the predecision evidence, not a fresh audit or a signature.

`SHA256SUMS` lists byte hashes of these ten files. From this directory, run `sha256sum -c SHA256SUMS` (or use a trusted local SHA-256 utility). This reads bytes only; it does **not** execute target code. Hashes show consistency with these copied files, not authenticity or semantic correctness. The receipt's internal `manifest_sha256` is a canonical JSON digest, distinct from the file's byte hash and does not cover this README or the supplement. The SHA list itself is not a signed attestation.

The selected artifacts were copied from saved, independently checked evidence without opening or changing the live database. Exact matching against the two current local keys found no matches before copying; this is not a comprehensive secret scan. No credential files or system logs were included.
