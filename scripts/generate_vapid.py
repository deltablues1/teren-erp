"""Generira VAPID ključeve za Web Push notifikacije.

Pokreni jednom:  py scripts/generate_vapid.py
Kopiraj output u .env datoteku.

Potrebno:  pip install py_vapid
"""
try:
    from py_vapid import Vapid
except ImportError:
    print("Instaliraj: pip install py_vapid")
    raise

v = Vapid()
v.generate_keys()

pub = v.public_key.public_bytes(
    __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding", "PublicFormat"])
    .Encoding.X962,
    __import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"])
    .PublicFormat.UncompressedPoint,
)
priv = v.private_key.private_bytes(
    __import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding", "PrivateFormat", "NoEncryption"])
    .Encoding.PEM,
    __import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"])
    .PrivateFormat.TraditionalOpenSSL,
    __import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"])
    .NoEncryption(),
)

import base64
pub_b64 = base64.urlsafe_b64encode(pub).rstrip(b"=").decode()

print("Kopiraj ovo u .env:\n")
print(f"VAPID_PUBLIC_KEY={pub_b64}")
print(f"VAPID_PRIVATE_KEY_PEM={priv.decode().strip()!r}")
print(f"VAPID_EMAIL=mailto:admin@sidcom.hr")
