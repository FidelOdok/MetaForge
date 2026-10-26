"""Why a public site would not give web.fetch its page, said so a model can act on it (FORGE-693).

Some vendor sites turn away clients that are not browsers, by design. Seen
from the sandbox on 2026-10-26 with web.fetch's own headers:

- www.st.com and www.mouser.com held the connection open and never answered
  (45 s read timeout); curl over HTTP/2 got the stream reset at once.
- DigiKey's product pages answered 403 with a Cloudflare challenge page
  ("Just a moment...").

web.fetch does not try to get past these: it identifies itself honestly and
takes no for an answer. What it must not do is fail with a bare transport
error, which a model reads as "the datasheet does not exist" or retries in a
loop. :func:`explain` names the site, how it refused, and the sources that do
work: the distributor APIs, the knowledge base, or a person's own download.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx


class SiteUnreachableError(Exception):
    """The site refused or never answered; the message is written for the model."""


_DO_NOT_RETRY = "Retrying the same URL will not help."

# Distributor web pages sit behind bot walls; their APIs are the supported way in.
_API_FOR_HOST: tuple[tuple[str, str], ...] = (
    ("digikey.", "DigiKey"),
    ("mouser.", "Mouser"),
)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or url).lower()


def _alternatives(host: str) -> str:
    for marker, name in _API_FOR_HOST:
        if marker in host:
            tool = name.lower()
            return (
                f"For {name} part data use the {name} API tools instead of its web pages: "
                f"{tool}.search / {tool}.get_product return the part's parameters and its "
                "datasheet link. " + _datasheet_sources()
            )
    return _datasheet_sources()


def _datasheet_sources() -> str:
    return (
        "For a datasheet value: try a distributor API (digikey.get_product, "
        "mouser.get_product) for the parameters and a datasheet link on another host, "
        "knowledge.search for a datasheet already ingested, or web.search for another "
        "copy of the same document. If none works, ask the person to download the PDF "
        "in a browser and add it with knowledge.ingest, and until then record any value "
        "you use as an assumption, not as sourced."
    )


def _is_challenge(response: httpx.Response) -> str | None:
    """Which bot wall answered, if the response is one."""
    if response.status_code not in (401, 403, 429, 503):
        return None
    server = response.headers.get("server", "").lower()
    head = response.text[:4000] if "html" in response.headers.get("content-type", "") else ""
    if response.headers.get("cf-mitigated") == "challenge" or (
        "cloudflare" in server and ("Just a moment" in head or "challenge-platform" in head)
    ):
        return "a Cloudflare bot challenge"
    if "akamai" in server or "Access Denied" in head:
        return "an Akamai access block"
    return None


def explain(url: str, exc: BaseException) -> str:
    """One message saying which site refused, how, and where to look instead."""
    host = _host(url)
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        wall = _is_challenge(response)
        if wall:
            return (
                f"{host} answered HTTP {response.status_code} with {wall}: it does not "
                f"serve automated readers. {_DO_NOT_RETRY} {_alternatives(host)}"
            )
        if response.status_code in (401, 403):
            return (
                f"{host} refused the request (HTTP {response.status_code}). "
                f"{_DO_NOT_RETRY} {_alternatives(host)}"
            )
        if response.status_code == 404:
            return f"{host} has no page at that address (HTTP 404). {_datasheet_sources()}"
        return (
            f"{host} answered HTTP {response.status_code} "
            f"({response.reason_phrase or 'error'}). {_alternatives(host)}"
        )
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"{host} did not answer in time. Some vendor sites (st.com and mouser.com "
            "among them) hold automated requests open without ever responding. "
            f"{_DO_NOT_RETRY} {_alternatives(host)}"
        )
    if isinstance(exc, httpx.RemoteProtocolError):
        return (
            f"{host} dropped the connection before answering, which is how some sites "
            f"turn away automated readers. {_DO_NOT_RETRY} {_alternatives(host)}"
        )
    if isinstance(exc, httpx.ConnectError):
        return f"could not connect to {host} ({exc}). {_alternatives(host)}"
    return f"could not read {host}: {type(exc).__name__}: {exc}. {_alternatives(host)}"
