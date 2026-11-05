"""S1: extract requirements, then deterministically check their source quotes."""
from pod.pipeline.context import RunContext
from pod.pipeline.prompts_extract import extractor_messages
from pod.schemas import RequirementExtraction
from pod.verify.history import item_from_requirement


async def extract_requirements(ctx: RunContext) -> None:
    async with ctx.stage("S1", "Extract requirements"):
        result = await ctx.llm_call(
            "extractor", extractor_messages(ctx.spec_text), RequirementExtraction,
            temperature=0.1, max_tokens=8000,
        )
        requirements = result.value.requirements
        if not requirements:
            raise ValueError("Extractor returned no requirements")
        used: set[str] = set()
        for req in requirements:
            if req.id in used:
                number = 1
                while f"REQ-{number:02d}" in used:
                    number += 1
                req = req.model_copy(update={"id": f"REQ-{number:02d}"})
            used.add(req.id)
            item = item_from_requirement(req, ctx.spec_text)
            ctx.state.requirements.append(item)
            ctx.emit("item_extracted", {"id": item.id, "kind": item.kind, "title": item.title,
                                        "source_verified": item.source_verified,
                                        "match": item.source_checks[0].match})
