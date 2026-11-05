"""Read pytest JUnit reports without executing target code."""

import xml.etree.ElementTree as ET


def parse_junit(xml: str | bytes) -> list[dict[str, str]]:
    root = ET.fromstring(xml)
    results = []
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        file = case.get("file", "")
        if file:
            # xunit1 preserves the actual path. The remaining classname suffix is
            # the class chain, irrespective of pytest's class naming conventions.
            module = file.removesuffix(".py").replace("/", ".").replace("\\", ".")
            prefix = module + "."
            classes = classname[len(prefix):].split(".") if classname.startswith(prefix) else []
            nodeid = "::".join([file, *classes, name])
        else:
            # xunit2 omits file: do not fabricate a module/class boundary.
            nodeid = f"{classname}::{name}".strip(":")
        outcome, message = "passed", ""
        for tag, value in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            element = case.find(tag)
            if element is not None:
                outcome = value
                message = element.get("message", "") or (element.text or "")
                break
        results.append({"nodeid": nodeid, "file": file, "name": name, "outcome": outcome, "message": message})
    return results
