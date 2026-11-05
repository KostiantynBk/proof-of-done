"""S2–S3: mine recurring rules and verify each against its cited record."""
from collections import Counter

from pod.pipeline.context import RunContext
from pod.pipeline.prompts_extract import miner_messages
from pod.schemas import RuleMining
from pod.verify.history import accept_rule, is_generic_advice


async def mine_rules(ctx: RunContext) -> None:
    proposed = []
    if ctx.history:
        async with ctx.stage("S2", "Mine rules"):
            result = await ctx.llm_call(
                "miner", miner_messages(ctx.history), RuleMining, temperature=0.1, max_tokens=6000,
            )
            proposed = result.value.rules
    else:
        ctx.skip_stage("S2", "Mine rules", "No history records")
    if not proposed and not ctx.state.requirements:
        ctx.skip_stage("S3", "Verify sources", "No sources to verify")
        return
    async with ctx.stage("S3", "Verify sources"):
        records = {record.record_id: record for record in ctx.history}
        reasons: Counter[str] = Counter()
        used: set[str] = set()
        for rule in proposed:
            if rule.id in used:
                number = 1
                while f"RULE-{number:02d}" in used:
                    number += 1
                rule = rule.model_copy(update={"id": f"RULE-{number:02d}"})
            used.add(rule.id)
            item = accept_rule(rule, records)
            # Standalone CI windows are not independent PRs or commits.
            units = set()
            for check in item.source_checks:
                record = records.get(check.record_id or "")
                if not check.verified or record is None:
                    continue
                if record.pr is not None:
                    units.add(f"pr:{record.pr}")
                elif record.kind == "commit":
                    units.add(record.record_id)
            item.distinct_prs = len(units)
            if len(units) < 2 and item.accepted:
                item = item.model_copy(update={"accepted": False, "source_verified": False,
                                                "drop_reason": "insufficient_independent_evidence"})
            if is_generic_advice(rule.rule) and item.accepted:
                item = item.model_copy(update={"accepted": False, "source_verified": False,
                                                "drop_reason": "generic_advice"})
            payload = {"id": item.id, "reason": item.drop_reason,
                       "distinct_prs": item.distinct_prs,
                       "verified_quotes": sum(c.verified for c in item.source_checks)}
            if item.accepted:
                ctx.state.rules.append(item)
                ctx.emit("item_extracted", {**payload, "kind": item.kind, "title": item.title,
                                            "source_verified": item.source_verified})
            else:
                ctx.state.dropped.append(item)
                reasons[item.drop_reason or "unknown"] += 1
                ctx.emit("item_dropped", payload)
        ctx.emit("sources_verified", {
            "requirements_verified": sum(i.source_verified for i in ctx.state.requirements),
            "requirements_total": len(ctx.state.requirements),
            "rules_accepted": len(ctx.state.rules), "rules_proposed": len(proposed),
            "dropped_by_reason": dict(reasons),
        })
