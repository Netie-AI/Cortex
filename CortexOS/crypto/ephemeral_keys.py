import binascii

from cryptography.hazmat.primitives.asymmetric import ed25519


class EphemeralKeyPair:
    def __init__(self, private_key=None):
        self.private_key = private_key or ed25519.Ed25519PrivateKey.generate()
        self.public_key = self.private_key.public_key()
        
def generate_session_key() -> EphemeralKeyPair:
    return EphemeralKeyPair()

def sign_manifest_data(manifest_id: str, dag_hash: str, keypair: EphemeralKeyPair) -> str:
    message = f"{manifest_id}:{dag_hash}".encode()
    signature = keypair.private_key.sign(message)
    return binascii.hexlify(signature).decode("utf-8")

def verify_manifest_data(manifest_id: str, dag_hash: str, signature_hex: str, keypair: EphemeralKeyPair) -> bool:
    try:
        message = f"{manifest_id}:{dag_hash}".encode()
        signature_bytes = binascii.unhexlify(signature_hex)
        keypair.public_key.verify(signature_bytes, message)
        return True
    except Exception:
        return False
