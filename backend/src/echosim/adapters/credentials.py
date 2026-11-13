from cryptography.fernet import Fernet, InvalidToken

from echosim.domain.accounts import Fault


class Credentials:
    def __init__(self, master_key):
        try:
            self.fernet = Fernet(master_key.encode())
        except Exception:
            raise ValueError("Invalid model master key") from None

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except (InvalidToken, AttributeError, UnicodeError):
            raise Fault("MODEL_CREDENTIAL_UNAVAILABLE", 503) from None
