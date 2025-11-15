from firecrawl import Firecrawl
from google import genai
import os, textwrap

FIRECRAWL_API_KEY = "fc-YOUR-API-KEY"
GENAI_API_KEY = os.getenv("GENAI_API_KEY") or "REPLACE_WITH_KEY"

firecrawl = Firecrawl(api_key=FIRECRAWL_API_KEY)
genai_client = genai.Client(api_key=GENAI_API_KEY)

DEFAULT_SCRAPE_OPTS = {
    "formats": ["markdown", "links"],
    "only_main_content": True,
    "timeout": 120000,
    "location": {"languages": ["en"]},
    "parsers": [],  # disable PDFs if you want to avoid extra cost
}

def safe_get_markdown(item):
    # handles SDK shapes: item may be dict-like or have nested 'data'
    if not item:
        return ""
    if isinstance(item, dict):
        for k in ("markdown", "data"):
            if k in item and isinstance(item[k], (str, dict)):
                if k == "markdown" and isinstance(item[k], str):
                    return item[k]
                if k == "data" and isinstance(item[k], dict):
                    return item[k].get("markdown", "") or ""
        return item.get("markdown", "") or ""
    # fallback
    return ""

def chunk_text(text, max_chars=3000):
    # naive chunking by characters; adjust to tokens if you have a tokenizer
    text = text.strip()
    if not text:
        return []
    chunks = []
    while text:
        chunk = text[:max_chars]
        # try to cut at last paragraph/newline for better context
        cut = chunk.rfind("\n\n")
        if cut > max_chars // 2:
            chunk = text[:cut]
        chunks.append(chunk.strip())
        text = text[len(chunk):].lstrip()
    return chunks

def summarize_chunk(chunk):
    prompt = textwrap.dedent(f"""
    Summarize this excerpt in 2-3 short sentences focused on car review insights: main issues, recurring problems, and positives.

    Excerpt:
    {chunk}
    """).strip()

    resp = genai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return getattr(resp, "text", repr(resp))

def summarize_document(md_text, url):
    chunks = chunk_text(md_text, max_chars=3000)
    if not chunks:
        return None
    chunk_summaries = [summarize_chunk(c) for c in chunks]
    # combine chunk summaries into a single per-document summary
    combined = "\n\n".join(chunk_summaries)
    synth_prompt = textwrap.dedent(f"""
    Combine these short summaries into a 3-4 sentence summary focused on the main drawbacks, common issues, and main positives.
    Preserve source attribution at the end: {url}

    Summaries:
    {combined}
    """).strip()
    resp = genai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=synth_prompt,
    )
    return getattr(resp, "text", repr(resp))

def search_and_summarize(query, limit=5, scrape_options=None):
    opts = DEFAULT_SCRAPE_OPTS.copy()
    if scrape_options:
        opts.update(scrape_options)
    # Firecrawl SDK: search(query, limit=..., scrape_options=...)
    results = firecrawl.search(query, limit=limit, scrape_options=opts)
    # results may be a list or dict depending on SDK – normalize
    items = results if isinstance(results, list) else results.get("data", results)
    if isinstance(items, dict) and "web" in items:
        items = items["web"]
    summaries = []
    for item in items:
        url = item.get("url") or item.get("sourceURL") or item.get("metadata", {}).get("sourceURL")
        md = safe_get_markdown(item)
        if not md:
            continue
        doc_summary = summarize_document(md, url)
        if doc_summary:
            summaries.append({"url": url, "summary": doc_summary})
    # synthesize final answer from per-document summaries
    combined_summaries = "\n\n".join([f"From {s['url']}:\n{s['summary']}" for s in summaries])
    final_prompt = textwrap.dedent(f"""
    Using the following per-document summaries, produce a 2-3 paragraph concise summary of Toyota Corolla (1997) reviews.
    Focus on: main drawbacks, common mechanical issues (with frequency), and main positives.
    For each major point, list the source URLs that support it.

    Documents:
    {combined_summaries}
    """).strip()
    resp = genai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=final_prompt,
    )
    final_text = getattr(resp, "text", repr(resp))
    return final_text, summaries

if __name__ == "__main__":
    final, sources = search_and_summarize("Toyota corolla 1997 buy guide", limit=3)
    print("FINAL SUMMARY:\n", final)
    print("\nSOURCES AND PER-DOC SUMMARIES:\n", sources)