from argon2 import PasswordHasher
from argon2.exceptions import VerificationError


class Passwords:
    def __init__(self):
        self.hasher = PasswordHasher()

    def hash(self, password):
        return self.hasher.hash(password)

    def verify(self, encoded, password):
        try:
            return self.hasher.verify(encoded, password)
        except VerificationError:
            return False
