"""Make a local HTTPS certificate so the phone allows camera, GPS and offline mode.

Browsers only allow those on secure origins. This creates a tiny private certificate authority (certs/rootCA.pem)
and a certificate for this laptop's LAN address. Install the CA on your phone once (README > Phone setup).
The CA key never leaves this laptop. Don't commit certs/.

    python scripts/make_cert.py              # auto-detects LAN IP
    python scripts/make_cert.py 192.168.1.42 # or name extra IPs / hostnames
"""
import datetime as dt
import ipaddress
import socket
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CERTS = Path(__file__).resolve().parent.parent / "certs"


def lan_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def write_key(key, path: Path):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def main():
    CERTS.mkdir(exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    ca_key_path, ca_path = CERTS / "rootCA-key.pem", CERTS / "rootCA.pem"

    if ca_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), None)
        ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
        print(f"Reusing CA {ca_path}")
    else:
        ca_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Deccan Rocks local CA ({socket.gethostname()})")])
        ca = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        write_key(ca_key, ca_key_path)
        ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        print(f"Created CA {ca_path}")

    hosts = {"localhost", "127.0.0.1", socket.gethostname(), f"{socket.gethostname()}.local", *sys.argv[1:]}
    if ip := lan_ip():
        hosts.add(ip)
    sans = []
    for h in sorted(hosts):
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            sans.append(x509.DNSName(h))

    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Deccan Rocks laptop")]))
        .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=397))
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    write_key(key, CERTS / "server-key.pem")
    (CERTS / "server.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Server cert for: {', '.join(sorted(hosts))}")
    print("Next: python run.py, then on the phone open https://<laptop-ip>:8443/rootCA.crt and install it.")


if __name__ == "__main__":
    main()
