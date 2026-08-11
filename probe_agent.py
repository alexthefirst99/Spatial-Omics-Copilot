"""Ask the agent anything and see exactly what it decided.

Usage (from the repo root):

    /opt/anaconda3/bin/python probe_agent.py "what pathways are enriched?"
    /opt/anaconda3/bin/python probe_agent.py            # runs a built-in set
    /opt/anaconda3/bin/python probe_agent.py -live "any papers on this?"
    /opt/anaconda3/bin/python probe_agent.py -prompt "explain this region"

By default the three tools are STUBBED, so nothing hits the network and it
returns instantly — you are checking the ROUTING decision.
-live makes real NCBI/PubMed calls (slow; pathway enrichment will report
unavailable unless gseapy is installed).
-prompt also dumps the full text sent to the LLM.
"""

import sys

sys.path.insert(0, "src")

LIVE = "-live" in sys.argv
SHOW_PROMPT = "-prompt" in sys.argv
questions = [a for a in sys.argv[1:] if not a.startswith("-")]

if not questions:
    questions = [
        # should gather everything
        "Explain what is happening in this region.",
        "Summarize the colorectal biology of this ROI.",
        "Is this region colorectal adenocarcinoma?",
        # should pick one tool
        "What pathways are enriched here?",
        "What does CEACAM5 do?",
        "Any published evidence for this?",
        # should use the image, no lookups
        "What does this region look like?",
        "Are there any glandular structures visible?",
        # should call nothing
        "Hello, can you help me?",
        "What is the weather today?",
        "Can you generate a report for my supervisor?",
    ]

ROI_GENES = [
    {"gene": "EPCAM", "log2_fold_change": 3.42},
    {"gene": "KRT20", "log2_fold_change": 3.01},
    {"gene": "CEACAM5", "log2_fold_change": 2.88},
    {"gene": "SPP1", "log2_fold_change": 2.55},
    {"gene": "COL1A1", "log2_fold_change": 2.31},
    {"gene": "KRAS", "log2_fold_change": 1.88},
]

calls = []

if not LIVE:
    import rag.copilot_agent.tools as T
    from rag.copilot_agent.models import STATUS_OK
    from rag.copilot_agent.tools import ToolOutcome

    def _stub(name):
        def run(genes, *a, **k):
            calls.append(name)
            return ToolOutcome(tool=name, status=STATUS_OK, detail="(stubbed)")

        return run

    T.run_pathway_tool = _stub("pathway_tool")
    T.run_pubmed_tool = _stub("pubmed_tool")
    T.run_gene_annotation_tool = _stub("gene_annotation_tool")

from rag.copilot_agent import run_agent  # noqa: E402

print(f"mode: {'LIVE (real network calls)' if LIVE else 'stubbed tools (routing only)'}")
print(f"ROI genes: {', '.join(g['gene'] for g in ROI_GENES)}\n")

for question in questions:
    calls.clear()
    result = run_agent(ROI_GENES, message=question, label="ROI 1")
    meta = result["metadata"]

    print("=" * 78)
    print(f"Q: {question}")
    print(f"   intent      : {meta['intent']}")
    print(f"   tools run   : {meta['tools_called'] or '(none)'}")
    print("   AGENT TRACE :")
    for step in meta["trace"]:
        detail = f"  — {step['detail']}" if step["detail"] else ""
        print(f"      [{step['status']:7s}] {step['step']}{detail}")
    if meta["pathways"]:
        print(f"   pathways    : {len(meta['pathways'])}")
    if meta["citations"]:
        print(f"   citations   : {[c['pmid'] for c in meta['citations']]}")
    if SHOW_PROMPT:
        print("   -- prompt sent to the LLM --")
        for line in result["context_str"].splitlines():
            print(f"   | {line}")
    print()
