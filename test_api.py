import requests
import json

base_url = "https://z2zelumxs4.execute-api.ap-southeast-2.amazonaws.com/altprod"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    "Origin": "https://www.igashop.com.au",
    "Referer": "https://www.igashop.com.au/"
}

# Try testing list stores `/stores` or `/v1/stores`
print("--- Testing /stores ---")
try:
    resp = requests.get(f"{base_url}/stores", headers=headers, params={"take": 5}, timeout=10)
    print("Status:", resp.status_code)
    print(resp.text[:1000])
except Exception as e:
    print("Error:", e)

# Try testing /v1/code/{code}/ShoppingModes/{shoppingModeId}/Stores
# with code = "2000", shoppingModeId = "22222222-2222-2222-2222-222222222222" (delivery)
print("\n--- Testing delivery stores for postcode 2000 ---")
url = f"{base_url}/v1/code/2000/ShoppingModes/22222222-2222-2222-2222-222222222222/Stores"
headers_with_geo = headers.copy()
headers_with_geo.update({
    "x-customer-address-latitude": "-33.8688",
    "x-customer-address-longitude": "151.2093"
})
try:
    resp = requests.get(url, headers=headers_with_geo, timeout=10)
    print("Status:", resp.status_code)
    print(resp.text[:1000])
except Exception as e:
    print("Error:", e)

