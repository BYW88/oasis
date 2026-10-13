"""Create local-only HTTPS and access credentials; never print private material."""
import argparse
import datetime
import ipaddress
import json
import os
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def initialize(root: Path, host: str, port: int):
    address = ipaddress.ip_address(host)
    if not address.is_private or address.is_unspecified or address.is_loopback:
        raise ValueError('Use a specific private LAN address')
    data = root / 'data'
    certs = data / 'certs'
    for folder in (data, certs, data / 'movies', data / 'temp', root / 'logs'):
        folder.mkdir(parents=True, exist_ok=True)
    config = root / 'lan-config.json'
    if config.exists():
        raise FileExistsError('Configuration already exists; do not replace credentials implicitly')
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Oasis LAN')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(address), x509.DNSName('localhost'),
            x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256()))
    (certs / 'server.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    (certs / 'server.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    os.environ['DB_PATH'] = str(data / 'oasis.db')
    sys.path.insert(0, str(root / 'app'))
    import auth
    if not auth.has_code():
        auth.enable()
    config.write_text(json.dumps({'host': host, 'port': port, 'frontend_port': 3031}, indent=2), encoding='utf-8')
    print(f'Initialized https://{host}:{port}; access code is in data/oasis.auth.json')
    print(f'Certificate SHA-256: {cert.fingerprint(hashes.SHA256()).hex()}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=8443)
    args = parser.parse_args()
    initialize(args.root.resolve(), args.host, args.port)
