import secrets

# Generate a 32-byte secure secret key
secret_key = secrets.token_hex(32)
print("Your secure Flask SECRET_KEY is:\n")
print(secret_key)
