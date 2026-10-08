"""Start home base: python run.py  (HTTPS on :8443 if certs/ exist, else HTTP on :8000)."""
import argparse
import socket

import uvicorn

from server.config import CERTS_DIR

ap = argparse.ArgumentParser()
ap.add_argument("--http", action="store_true", help="plain HTTP even if certs exist (Android flag setup)")
ap.add_argument("--port", type=int)
ap.add_argument("--reload", action="store_true")
args = ap.parse_args()

cert, key = CERTS_DIR / "server.pem", CERTS_DIR / "server-key.pem"
https = cert.exists() and key.exists() and not args.http
port = args.port or (8443 if https else 8000)

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("10.255.255.255", 1))
    ip = s.getsockname()[0]
except OSError:
    ip = "127.0.0.1"
scheme = "https" if https else "http"
print(f"\n  Laptop: {scheme}://localhost:{port}\n  Phone:  {scheme}://{ip}:{port}   (same Wi-Fi)\n")
if not https:
    print("  No certs: the phone will block camera/GPS/offline on plain HTTP. See README > Phone setup.\n")

uvicorn.run(
    "server.main:app", host="0.0.0.0", port=port, reload=args.reload,
    ssl_certfile=str(cert) if https else None, ssl_keyfile=str(key) if https else None,
)
