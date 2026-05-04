from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen


def _norm(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^\w]+", " ", normalized.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _results_url(url: str) -> str:
    if "#!/" in url:
        prefix = url.split("#!/", 1)[0]
        return f"{prefix}#!/results"
    return f"{url.rstrip('/')}#!/results"


def _http_get_text(url: str, timeout_ms: int) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html,application/json"})
    with urlopen(request, timeout=max(1, timeout_ms // 1000)) as response:
        return response.read().decode("utf-8", "ignore")


def _http_get_json(url: str, timeout_ms: int) -> dict[str, object]:
    text = _http_get_text(url, timeout_ms)
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return payload


def _extract_event_id(html: str) -> str:
    match = re.search(r"window\.SailingInfo\.eventId\s*=\s*'([^']+)'", html)
    if not match:
        raise ValueError("Could not locate event ID in page HTML.")
    return match.group(1)


def _extract_bootstrap_data(html: str) -> dict[str, object]:
    match = re.search(
        r"window\.boostrapedResourceData\s*=\s*(\{[\s\S]*?\})\s*;\s*</script>",
        html,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("Could not locate bootstrap data in page HTML.")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("Bootstrap data is not a JSON object.")
    return data


def _extract_regatta_map(bootstrap_data: dict[str, object]) -> dict[str, dict[str, str]]:
    regattas = bootstrap_data.get("Regatta")
    if not isinstance(regattas, list):
        raise ValueError("Bootstrap data does not contain a regatta list.")

    mapping: dict[str, dict[str, str]] = {}
    for item in regattas:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or "").strip()
        regatta_id = str(item.get("Id") or "").strip()
        if not name or not regatta_id:
            continue
        mapping[_norm(name)] = {"name": name, "id": regatta_id}

    if not mapping:
        raise ValueError("No regattas with Name/Id found in bootstrap data.")
    return mapping


def _resolve_class_requests(
    class_names: Iterable[str],
    regatta_map: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    resolved: dict[str, dict[str, str]] = {}
    for class_name in class_names:
        requested = str(class_name).strip()
        if not requested:
            continue
        key = _norm(requested)
        regatta = regatta_map.get(key)
        if regatta is None:
            raise ValueError(f"Class '{requested}' was not found in event regatta list.")
        resolved[requested] = regatta

    if not resolved:
        raise ValueError("No valid class names were provided.")
    return resolved


def fetch_event_bootstrap(
    event_url: str,
    timeout_ms: int = 90000,
) -> tuple[str, dict[str, dict[str, str]]]:
    """Fetch event page bootstrap data and return event ID plus class->regatta mapping."""
    html = _http_get_text(_results_url(event_url), timeout_ms)
    event_id = _extract_event_id(html)
    bootstrap_data = _extract_bootstrap_data(html)
    regatta_map = _extract_regatta_map(bootstrap_data)
    return event_id, regatta_map


def fetch_class_results_batch(
    event_url: str,
    class_names: list[str],
    timeout_ms: int = 90000,
    max_workers: int = 8,
) -> dict[str, dict[str, object]]:
    """Fetch regatta result JSON for all requested classes in parallel."""
    event_id, regatta_map = fetch_event_bootstrap(event_url, timeout_ms=timeout_ms)
    requests_by_class = _resolve_class_requests(class_names, regatta_map)

    results: dict[str, dict[str, object]] = {}

    def _fetch_one(class_name: str) -> tuple[str, dict[str, object]]:
        regatta = requests_by_class[class_name]
        endpoint = f"https://www.manage2sail.com/api/event/{event_id}/regattaresult/{regatta['id']}"
        payload = _http_get_json(endpoint, timeout_ms)
        return class_name, payload

    worker_count = max(1, min(max_workers, len(requests_by_class)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for class_name, payload in executor.map(_fetch_one, requests_by_class.keys()):
            results[class_name] = payload

    return {class_name: results[class_name] for class_name in class_names if class_name in results}
