import io
import socket
from types import SimpleNamespace

import pytest
import requests
from fastapi.testclient import TestClient
from PIL import Image
from requests.exceptions import InvalidURL
from urllib3.response import HTTPResponse

import api
import auth
import catalog
import safe_http


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv('OASIS_TRUST_LOOPBACK', raising=False)
    monkeypatch.setattr(auth, 'AUTH_PATH', str(tmp_path / 'auth.json'))
    monkeypatch.setattr(catalog, 'DB_PATH', str(tmp_path / 'catalog.db'))
    monkeypatch.setattr(api.db_setup, 'DB_PATH', str(tmp_path / 'catalog.db'))
    auth._failures.clear()
    with TestClient(api.app, base_url='http://localhost:8000', client=('127.0.0.1', 12345)) as client:
        yield client
    auth._failures.clear()


def test_loopback_and_forged_host_need_code(client):
    assert auth.has_code()
    assert len(auth.normalize(auth.get_code())) == 26
    assert client.get('/api/videos', headers={'X-Oasis-Client': '1'}).status_code == 401
    headers = {'X-Oasis-Client': '1', 'Authorization': f'Bearer {auth.get_code()}'}
    assert client.get('/api/videos', headers=headers).status_code == 200
    assert client.post('/api/update/apply', headers=headers).status_code == 403
    assert client.delete('/api/auth/remote', headers=headers).status_code == 403


def test_forwarded_header_cannot_change_bucket():
    def req(xff):
        return SimpleNamespace(client=SimpleNamespace(host='192.0.2.1'), headers={'x-forwarded-for': xff})
    assert auth.client_key(req('one')) == auth.client_key(req('two'))


def test_failure_storage_is_bounded(monkeypatch):
    monkeypatch.setattr(auth, '_failures', {str(i): (1, 10**12) for i in range(4096)})
    auth.register_failure('new')
    assert len(auth._failures) == 4096


@pytest.mark.parametrize('address', ['127.0.0.2', '::1', '192.168.1.1', '10.0.0.1',
    '169.254.169.254', '172.16.0.1', '0.0.0.0', '::ffff:127.0.0.1'])
def test_private_destinations_rejected(address, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, 1, 6, '', (address, 80))])
    with pytest.raises(InvalidURL):
        safe_http.destination('http://media.example/image')


def test_mixed_public_private_dns_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, 1, 6, '', ('8.8.8.8', 443)),
        (socket.AF_INET, 1, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(InvalidURL):
        safe_http.destination('https://media.example/image')


def test_connection_pins_ip_but_keeps_tls_hostname(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, 1, 6, '', ('8.8.8.8', 443))])
    adapter = safe_http.HTTPAdapter()
    recorded = {}
    def pool(**kwargs):
        recorded.update(kwargs)
        return object()
    monkeypatch.setattr(adapter.poolmanager, 'connection_from_host', pool)
    request = requests.Request('GET', 'https://media.example/image').prepare()
    adapter.get_connection_with_tls_context(request, True)
    assert recorded['host'] == '8.8.8.8'
    assert recorded['pool_kwargs']['server_hostname'] == 'media.example'
    assert recorded['pool_kwargs']['assert_hostname'] == 'media.example'
    assert request.headers['Host'] == 'media.example'


def test_redirect_to_loopback_is_rejected(monkeypatch):
    def dns(host, *a, **k):
        address = '127.0.0.1' if host == '127.0.0.1' else '8.8.8.8'
        return [(socket.AF_INET, 1, 6, '', (address, 443))]
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    class Pool:
        def urlopen(self, *a, **k):
            return HTTPResponse(body=io.BytesIO(b''), status=302,
                headers={'Location': 'http://127.0.0.1/private'}, preload_content=False)
    with safe_http.Session() as session:
        for scheme in ['http://', 'https://']:
            monkeypatch.setattr(session.adapters[scheme].poolmanager, 'connection_from_host', lambda **k: Pool())
        with pytest.raises(InvalidURL):
            session.get('https://media.example/image')


class CoverResponse:
    def __init__(self, data, mime):
        self.data, self.headers = data, {'Content-Type': mime}
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def raise_for_status(self): pass
    def iter_content(self, size): yield self.data


@pytest.mark.parametrize('mime', ['image/svg+xml', 'image/png'])
def test_svg_and_mislabeled_svg_are_refused(monkeypatch, mime):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>/* marker */</script></svg>'
    monkeypatch.setattr(safe_http, 'get', lambda *a, **k: CoverResponse(svg, mime))
    assert catalog.fetch_cover_image('https://media.example/cover') is None


def test_raster_is_reencoded(monkeypatch):
    original = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(original, format='PNG')
    monkeypatch.setattr(safe_http, 'get', lambda *a, **k: CoverResponse(original.getvalue(), 'image/png'))
    data, mime = catalog.fetch_cover_image('https://media.example/cover')
    assert mime == 'image/jpeg'
    assert Image.open(io.BytesIO(data)).format == 'JPEG'


def test_legacy_svg_blob_not_served(client, monkeypatch):
    monkeypatch.setattr(catalog, 'get_cover_source', lambda _: {
        'data': b'<svg/>', 'mime': 'image/svg+xml', 'cover_url': None})
    response = client.get('/api/stream/cover/1', params={'token': auth.get_code()})
    assert response.status_code == 404


def test_cover_has_private_cache_and_sandbox(client, monkeypatch):
    monkeypatch.setattr(catalog, 'get_cover_source', lambda _: {
        'data': b'jpeg-marker', 'mime': 'image/jpeg', 'cover_url': None})
    response = client.get('/api/stream/cover/1', params={'token': auth.get_code()})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'private, no-store'
    assert 'sandbox' in response.headers['content-security-policy']


def test_anime1_substring_url_not_fetched(client, monkeypatch):
    monkeypatch.setattr(catalog, 'get_video_by_id', lambda _: {'url': 'http://127.0.0.1/?anime1.me'})
    monkeypatch.setattr(api, 'get_anime1_stream_info', lambda _: pytest.fail('Unexpected HTTP request'))
    response = client.get('/api/stream/online/1', params={'token': auth.get_code()})
    assert response.status_code == 400


def test_translation_is_local_by_default(monkeypatch):
    monkeypatch.delenv('OASIS_ENABLE_TRANSLATION', raising=False)
    assert catalog.translate_to_zh_tw('日本語テスト') == '日本語テスト'


def test_non_ascii_code_fails_without_server_error(client):
    response = client.post('/api/auth/login', headers={'X-Oasis-Client': '1'}, json={'code': '测试'})
    assert response.status_code == 401
