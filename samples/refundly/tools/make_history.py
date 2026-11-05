"""Generate reproducible synthetic review, commit, and CI history."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "history"
comments = [
    (101, "Use Decimal and money helpers for amounts, never float."),
    (104, "Please use the money helpers and Decimal rather than float for payment amounts."),
    (109, "Convert amounts with Decimal and money helpers, not float arithmetic."),
    (102, "Use clock.now() instead of datetime.now() for all current timestamps."),
    (108, "Please call clock.now() rather than datetime.utcnow() so time is injectable."),
    (103, "Write audit events with audit.record(), not logger.info or print."),
    (110, "Please use audit.record() for audit events rather than printing them."),
    (105, "Raise errors from refundly.errors instead of bare ValueError or Exception."),
    (111, "Use the refundly.errors hierarchy, not a bare ValueError, for rejections."),
    (112, "I prefer a blue dashboard header for the admin view."),
    (112, "Could the admin header be blue rather than gray?"),
]
noise = ["The variable name could be clearer here.", "There is a typo in this docstring.", "LGTM", "What happens if the order is archived?", "Could this heading be shorter?", "This comma looks misplaced.", "Can you explain this example?", "The diagram has a missing arrow.", "Please rename this local counter.", "Thanks for the helpful example.", "Is the example currency EUR?", "Maybe use a smaller icon here.", "The list has one extra blank line.", "Does this link need an anchor?", "Consider a more descriptive title.", "This section seems out of date.", "Would a table help this paragraph?", "The screenshot is slightly blurry.", "One example seems duplicated.", "Nice explanation of the migration.", "Could you expand this abbreviation?", "Should this include a date?", "The diagram label is misaligned.", "Perhaps mention the optional field.", "I wonder whether this can be shorter.", "A spelling correction is needed.", "The bullet indentation looks unusual.", "What does this abbreviation mean?", "This example could use a caption.", "Is this link still relevant?", "The text wraps oddly here.", "Thank you for checking this."]
comments.extend((200 + i, text) for i, text in enumerate(noise))
records = [{"id": f"review-{i:03}", "pr": pr, "pr_title": f"Refund workflow change {pr}", "author": f"reviewer-{i % 5 + 1}", "path": "refundly/refunds.py", "line": 20 + i, "body": body, "created_at": f"2026-10-{(i % 28) + 1:02}T10:00:00Z"} for i, (pr, body) in enumerate(comments, 1)]
(ROOT / "reviews.json").write_text(json.dumps(records, indent=2) + "\n", encoding="ascii")
blocks = []
for i in range(25):
    title = 'Revert "Use datetime.now() directly"' if i == 7 else f"Refund workflow maintenance {i + 1}"
    blocks.append(f"commit {hashlib.sha1(f'refundly-{i + 1}-{title}'.encode()).hexdigest()}\nAuthor: contributor-{i % 4 + 1}\nDate: 2026-10-{i + 1:02} 10:00:00 +0000\n\n    {title}\n\n refundly/refunds.py | 2 +-\n 1 file changed, 1 insertion(+), 1 deletion(-)\n")
(ROOT / "git-log.txt").write_text("\n".join(blocks), encoding="ascii")
(ROOT / "ci" / "run-1042.txt").write_text("CI run 1042\nFAILED test_age_boundary: datetime.now() crossed midnight while the fixture expected a fixed time.\nRerun passed. Inject clock.now() to avoid wall clock dependence.\n", encoding="ascii")
