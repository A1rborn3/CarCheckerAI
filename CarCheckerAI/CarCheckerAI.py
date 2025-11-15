from firecrawl import Firecrawl
from google import genai
import os, textwrap, json
from dotenv import load_dotenv
from pathlib import Path

# Load .env: prefer project root .env (one level up from script)
env_path = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=env_path)

FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY")
GENAI_API_KEY = os.getenv("GENAI_API_KEY")

if not FIRECRAWL_API_KEY or not GENAI_API_KEY:
    raise RuntimeError(f"FIRECRAWL_API_KEY and GENAI_API_KEY must be set in environment or .env (tried {env_path})")

firecrawl = Firecrawl(api_key=FIRECRAWL_API_KEY)
genai_client = genai.Client(api_key=GENAI_API_KEY)

DEFAULT_SCRAPE_OPTS = {
    "formats": ["markdown", "links"],
    "only_main_content": True,
    "timeout": 120000,
    "location": {"languages": ["en"]},
    "parsers": [],  # disable PDFs if you want to avoid extra cost
}

def _model_to_dict(obj):
    if not obj:
        return {}
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    return {}

def safe_get_markdown(item):
    if not item:
        return ""
    if isinstance(item, dict):
        return item.get("markdown") or (item.get("data") or {}).get("markdown", "") or ""
    md = getattr(item, "markdown", None)
    if isinstance(md, str):
        return md
    if hasattr(item, "model_dump") or hasattr(item, "dict"):
        d = _model_to_dict(item)
        return d.get("markdown") or (d.get("data") or {}).get("markdown", "") or ""
    return ""

def safe_get_url(item):
    if not item:
        return None
    if isinstance(item, dict):
        return (
            item.get("url")
            or item.get("sourceURL")
            or (item.get("metadata") or {}).get("sourceURL")
            or (item.get("metadata") or {}).get("url")
            or (item.get("metadata") or {}).get("source_url")
        )
    url = getattr(item, "url", None) or getattr(item, "sourceURL", None)
    if url:
        return url
    md = getattr(item, "metadata", None)
    if md:
        return getattr(md, "source_url", None) or getattr(md, "sourceURL", None) or getattr(md, "url", None) or getattr(md, "og_url", None)
    if hasattr(item, "model_dump") or hasattr(item, "dict"):
        d = _model_to_dict(item)
        return (
            d.get("url")
            or d.get("sourceURL")
            or (d.get("metadata") or {}).get("sourceURL")
            or (d.get("metadata") or {}).get("url")
            or (d.get("metadata") or {}).get("source_url")
        )
    return None

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

def _compress_for_single_request(text, max_chars=20000):
    """
    If text <= max_chars return as-is.
    If longer, create a single reduced document by taking start/middle/end slices so we keep
    representative content but still send one request.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    part = max_chars // 3
    start = text[:part]
    middle_index = len(text) // 2
    middle = text[middle_index - part//2: middle_index + part//2]
    end = text[-part:]
    return "\n\n--START--\n\n" + start + "\n\n--MIDDLE--\n\n" + middle + "\n\n--END--\n\n" + end

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
    if not md_text:
        return None
    payload_text = _compress_for_single_request(md_text, max_chars=20000)
    prompt = textwrap.dedent(f"""
    Produce a concise summary of the following document in 2 short paragraphs (2-5 sentences each).
    Focus on: main drawbacks, common mechanical issues (mention relative frequency if present), and main positives.
    End with a single-line source attribution exactly as: Source: {url}

    Document:
    {payload_text}
    """).strip()
    resp = genai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    return getattr(resp, "text", repr(resp))

def extract_specs_from_docs(specs_texts, urls):
    """
    Single genAI call to extract structured specs from combined spec docs.
    Return a list/dict of extracted spec entries (best-effort).
    """
    combined = "\n\n---\n\n".join([f"URL: {u}\n\n{t}" for u, t in zip(urls, specs_texts)])
    prompt = textwrap.dedent(f"""
    You are given several specification documents for a car. For each document, extract the following fields if present:
    - brand
    - model
    - year_range
    - engine_type_and_displacement
    - horsepower
    - torque
    - fuel_economy
    - acceleration
    - top_speed
    - notable_features (comma separated list)

    Output a JSON array with one object per source in the same order. Each object must include "source_url" and the fields above (use null for missing fields). Example:
    [
      {{
        "source_url": "...",
        "brand": "...",
        "model": "...",
        "year_range": "...",
        "engine_type_and_displacement": "...",
        "horsepower": "...",
        "torque": "...",
        "fuel_economy": "...",
        "acceleration": "...",
        "top_speed": "...",
        "notable_features": "..."
      }},
      ...
    ]

    Documents:
    {combined}
    """).strip()
    resp = genai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )
    text = getattr(resp, "text", repr(resp))
    try:
        parsed = json.loads(text)
        return parsed
    except Exception:
        # model didn't return strict JSON; return raw text as fallback
        return {"raw": text}

def normalize_results(results):
    if isinstance(results, list):
        return results
    if isinstance(results, dict):
        return results.get("data") or results.get("web") or results
    if hasattr(results, "web"):
        return getattr(results, "web") or []
    try:
        d = _model_to_dict(results)
        return d.get("data") or d.get("web") or []
    except Exception:
        return []

def search_and_summarize(querySearch, querySpecs, searchLimit, specLimit, scrape_options=None, max_genai_calls=10, car_type=None):
    """
    Strategy to remain under max_genai_calls:
      - 1 genAI call to extract specs (combined spec docs)
      - N genAI calls (<= max_genai_calls - 2) one per document summary
      - 1 genAI call to synthesize final report
    """
    print("Running")
    if scrape_options:
        opts = DEFAULT_SCRAPE_OPTS.copy()
        opts.update(scrape_options)
    else:
        opts = DEFAULT_SCRAPE_OPTS.copy()

    # Reserve 2 calls: one for specs extraction and one final synthesis
    allowed_doc_calls = max(0, max_genai_calls - 2)
    doc_limit = min(searchLimit, allowed_doc_calls)

    # Firecrawl searches
    results = firecrawl.search(query=querySearch, limit=doc_limit, scrape_options=opts)
    resultsSpecs = firecrawl.search(query=querySpecs, limit=specLimit, scrape_options=opts)

    items = normalize_results(results)
    SpecsItems = normalize_results(resultsSpecs)
    print("FireCrawl done")
    if isinstance(items, dict) and "web" in items:
        items = items["web"]

    # Per-document summaries (bounded by doc_limit)
    summaries = []
    for item in items:
        if len(summaries) >= doc_limit:
            break
        url = safe_get_url(item)
        md = safe_get_markdown(item)
        if not md:
            continue
        doc_summary = summarize_document(md, url)
        if doc_summary:
            summaries.append({"url": url, "summary": doc_summary})

    # Specs: combine spec markdown and do single extraction call
    specs_texts = []
    specs_urls = []
    for s in SpecsItems[:specLimit]:
        url = safe_get_url(s)
        md = safe_get_markdown(s)
        if md:
            specs_texts.append(md)
            specs_urls.append(url or "unknown")
    specs_structured = extract_specs_from_docs(specs_texts, specs_urls) if specs_texts else []

    # Synthesize final answer with a single final call
    combined_summaries = "\n\n".join([f"From {s['url']}:\n{s['summary']}" for s in summaries])
    specsDoc = json.dumps(specs_structured, indent=2) if specs_texts else "No specs found."

    # Determine a readable car_type for the prompt
    car_label = car_type or querySearch
    print("Summaries done, writing report")
    final_prompt = textwrap.dedent(f"""
    Using the following per-document summaries and the extracted specification data, produce a 2-3 paragraph concise summary of {car_label} reviews.
    Focus on: main drawbacks, common mechanical issues (with frequency), and main positives.
    Structure the response exactly as follows:

    Brand: [Car Brand]
    Model: [Car Model]
    Year: [Car Year range]

    Specifications:
    [list of main specifications from spec sheets: engine type and displacement, horsepower, torque, fuel economy, acceleration, top speed, notable features]

    General Overview:
    [Overview paragraph]
    Key Takeaways:
    - [takeaway 1]
    - [takeaway 2]

    Main Issues and Drawbacks:
    [Issues paragraph]
    Key Takeaways:
    - [takeaway 1]
    - [takeaway 2]

    Main Positives:
    [Positives paragraph]
    Key Takeaways:
    - [takeaway 1]
    - [takeaway 2]

    TLDR:
    [2-4 sentence summary]

    Sources:
    [list of source URLs supporting major points]

    Documents:
    {combined_summaries}

    Extracted Specifications (JSON):
    {specsDoc}
    """).strip()

    resp = genai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=final_prompt,
    )
    final_text = getattr(resp, "text", repr(resp))
    return final_text, summaries

if __name__ == "__main__":
    car_type = "porsche cayman 2007"
    searchLimit = 5
    specLimit = 3
    final, sources = search_and_summarize(f"{car_type} buy guide", f"{car_type} spec sheet carfolio", searchLimit, specLimit, max_genai_calls=10, car_type=car_type)
    print("FINAL SUMMARY:\n", final)