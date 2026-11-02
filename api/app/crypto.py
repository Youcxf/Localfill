import os

from cryptography.fernet import Fernet


# encrypts the tradovate passwords before they go in the database
# the key is created on first run and stored next to the database
class Crypto:
    def __init__(self, key_path):
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                key = f.read()
        else:
            key = Fernet.generate_key()
            with open(key_path, "wb") as f:
                f.write(key)
            # only the owner should read it (does nothing on windows)
            os.chmod(key_path, 0o600)
        self.fernet = Fernet(key)

    def encrypt(self, text):
        return self.fernet.encrypt(text.encode()).decode()

    def decrypt(self, token):
        return self.fernet.decrypt(token.encode()).decode()
