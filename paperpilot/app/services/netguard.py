from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit


class UnsafeURLError(ValueError):
    pass


TRUSTED_HOSTS = {
    "api.github.com",
    "raw.githubusercontent.com",
    "api.semanticscholar.org",
    "api.openalex.org",
    "api.core.ac.uk",
}


def _allow_private() -> bool:
    return os.getenv("PAPERPILOT_ALLOW_PRIVATE_FETCH", "").strip().lower() in {"1", "true", "yes"}


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = []
    for info in infos:
        try:
            addresses.append(info[4][0])
        except (IndexError, TypeError):
            continue
    return addresses


def validate_url(
    url: str,
    *,
    allow_private: bool | None = None,
    resolver=None,
) -> str:
    """Allow only public HTTPS URLs with no userinfo and no custom port."""
    if allow_private is None:
        allow_private = _allow_private()
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise UnsafeURLError("URL 格式不正确。") from exc
    if parts.scheme.lower() != "https":
        raise UnsafeURLError("只支持 HTTPS 地址。")
    if not parts.hostname:
        raise UnsafeURLError("URL 缺少域名。")
    if parts.username or parts.password:
        raise UnsafeURLError("URL 不能包含账号或密码。")
    if parts.port not in (None, 443):
        raise UnsafeURLError("只支持 443 端口。")
    host = parts.hostname.strip().lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal", ".lan")):
        if not allow_private:
            raise UnsafeURLError("不允许访问内网或本机地址。")
    if allow_private:
        return text
    # Trusted academic/API hosts may resolve through a local proxy in managed environments.
    if host in TRUSTED_HOSTS:
        return text
    try:
        parsed_ip = ipaddress.ip_address(host)
        addresses = [str(parsed_ip)]
    except ValueError:
        resolver = resolver or _default_resolver
        try:
            raw_addresses = resolver(host)
        except Exception as exc:
            raise UnsafeURLError("域名解析失败，无法确认目标地址。") from exc
        addresses = []
        for item in raw_addresses:
            if isinstance(item, str):
                addresses.append(item)
            elif isinstance(item, (tuple, list)):
                try:
                    addresses.append(item[4][0])
                except (IndexError, TypeError):
                    continue
        if not addresses:
            raise UnsafeURLError("域名解析失败，无法确认目标地址。")
    if not allow_private:
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError as exc:
                raise UnsafeURLError("目标地址无法识别。") from exc
            if not ip.is_global:
                raise UnsafeURLError("不允许访问内网、回环或保留地址。")
    return text
