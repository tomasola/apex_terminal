import ccxt
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("OKX_API_KEY")
api_secret = os.environ.get("OKX_API_SECRET")
passphrase = os.environ.get("OKX_PASSPHRASE")

print(f"Testing with Key: '{api_key}' (Length: {len(api_key)})")
print(f"Testing with Secret: '{api_secret}' (Length: {len(api_secret)})")
print(f"Testing with Passphrase: '{passphrase}' (Length: {len(passphrase)})")

def test_connection(testnet, secret_to_use):
    print(f"\n--- Testing OKX (Testnet={testnet}, Secret={secret_to_use[:5]}...) ---")
    exchange = ccxt.okx({
        'apiKey': api_key,
        'secret': secret_to_use,
        'password': passphrase,
        'enableRateLimit': True,
        'verbose': False,
        'options': {
            'defaultType': 'swap',
            'adjustForTimeDifference': True
        }
    })
    if testnet:
        exchange.set_sandbox_mode(True)
    
    try:
        # print(f"API Base URL: {exchange.urls['api']}")
        # print(f"Fetching Private Balance...")
        balance = exchange.fetch_balance()
        print("Success! Balance fetched.")
        return True
    except Exception as e:
        print(f"Error Detail: {e}")
        return False

# Try LIVE with provided case
print("--- Testing Original Secret ---")
test_connection(False, api_secret)

# Try LIVE with lowercase secret
print("\n--- Testing Lowercase Secret ---")
test_connection(False, api_secret.lower())
