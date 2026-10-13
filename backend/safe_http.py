"""Public-network HTTP transport for URLs obtained from catalog/media metadata.

Every request (including redirects) resolves and validates its destination, then
connects to the selected numeric address. TLS still verifies the original host.
Environment proxies are disabled because they would bypass destination pinning.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter as BaseHTTPAdapter
from requests.exceptions import InvalidURL


def destination(url: str) -> tuple[str, str, int, str]:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('HTTP(S) URL required')
        if parsed.username is not None or parsed.password is not None:
            raise ValueError('URL credentials are not supported')
        host = parsed.hostname.encode('idna').decode('ascii')
        if '%' in host:
            raise ValueError('Scoped addresses are not supported')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError('Non-public destination refused')
        return parsed.scheme, host, port, addresses[0]
    except (ValueError, UnicodeError, OSError) as exc:
        raise InvalidURL(f'Unsafe destination: {exc}') from exc


class HTTPAdapter(BaseHTTPAdapter):
    def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
        if proxies and any(proxies.values()):
            raise InvalidURL('Proxies are disabled for metadata/media requests')
        if verify is False:
            raise InvalidURL('TLS verification cannot be disabled')
        scheme, host, port, address = destination(request.url)
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        host_params.update(host=address, port=port, scheme=scheme)
        if scheme == 'https':
            pool_kwargs.update(server_hostname=host, assert_hostname=host)
        authority = f'[{host}]' if ':' in host else host
        if port != (443 if scheme == 'https' else 80):
            authority += f':{port}'
        # Replaced on every redirect; never carry a prior hop's Host forward.
        request.headers['Host'] = authority
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)


class Session(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False
        self.max_redirects = 5
        self.mount('http://', HTTPAdapter())
        self.mount('https://', HTTPAdapter())


def request(method, url, **kwargs):
    # Keep streamed responses alive; Response.close() releases their connection.
    session = Session()
    try:
        response = session.request(method, url, **kwargs)
    except BaseException:
        session.close()
        raise
    if not kwargs.get('stream'):
        session.close()
    return response


def get(url, **kwargs):
    return request('GET', url, **kwargs)


def post(url, **kwargs):
    return request('POST', url, **kwargs)
