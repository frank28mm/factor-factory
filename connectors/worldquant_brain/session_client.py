from __future__ import annotations

import json
import urllib.parse
import urllib.request


PROXY_BASE = "http://localhost:3457"
API_BASE = "https://api.worldquantbrain.com"
WORLDQUANT_HOST = "platform.worldquantbrain.com"


def http_json(url: str, data: str | None = None) -> dict | list:
    body = data.encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=body)
    if data is not None:
        request.add_header("Content-Type", "text/plain;charset=UTF-8")
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def find_worldquant_target_id() -> str:
    targets = http_json(f"{PROXY_BASE}/targets")
    if not isinstance(targets, list):
        raise RuntimeError("Unexpected CDP /targets payload.")
    for target in targets:
        url = str(target.get("url", ""))
        if WORLDQUANT_HOST in url:
            return str(target["targetId"])
    raise RuntimeError("No open WorldQuant BRAIN tab found in the logged-in browser.")


def resolve_api_url(endpoint_or_url: str) -> str:
    if endpoint_or_url.startswith("http"):
        return endpoint_or_url
    return f"{API_BASE}{endpoint_or_url}"


def cdp_eval(target_id: str, expression: str) -> str:
    payload = http_json(f"{PROXY_BASE}/eval?target={urllib.parse.quote(target_id)}", data=expression)
    if not isinstance(payload, dict) or "value" not in payload:
        raise RuntimeError(f"Unexpected /eval payload: {payload!r}")
    return str(payload["value"])


def browser_fetch_response(
    target_id: str,
    endpoint_or_url: str,
    *,
    method: str = "GET",
    json_body: dict | list | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    api_url = resolve_api_url(endpoint_or_url)
    merged_headers = {"Accept": "application/json, text/plain, */*"}
    if json_body is not None:
        merged_headers["Content-Type"] = "application/json"
    if headers:
        merged_headers.update(headers)

    body_literal = "undefined"
    if json_body is not None:
        body_literal = json.dumps(json.dumps(json_body, ensure_ascii=False))

    expression = f"""
(() => {{
  const options = {{
    method: {json.dumps(method)},
    credentials: 'include',
    headers: {json.dumps(merged_headers, ensure_ascii=False)}
  }};
  if ({'true' if json_body is not None else 'false'}) {{
    options.body = {body_literal};
  }}
  return fetch({json.dumps(api_url)}, options).then(async (response) => {{
    const text = await response.text();
    const headers = {{
      location: response.headers.get('location'),
      contentType: response.headers.get('content-type'),
      retryAfter: response.headers.get('retry-after')
    }};
    return JSON.stringify({{
      ok: response.ok,
      status: response.status,
      url: response.url,
      headers,
      text
    }});
  }});
}})()
""".strip()
    return json.loads(cdp_eval(target_id, expression))


def browser_fetch_json_response(
    target_id: str,
    endpoint_or_url: str,
    *,
    method: str = "GET",
    json_body: dict | list | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    response = browser_fetch_response(
        target_id,
        endpoint_or_url,
        method=method,
        json_body=json_body,
        headers=headers,
    )
    text = str(response.get("text", "") or "")
    try:
        payload = json.loads(text) if text else None
    except json.JSONDecodeError:
        payload = None
    response["payload"] = payload
    return response
