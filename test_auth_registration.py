import time
import requests

BASE_URL = "http://localhost:8000"

def test_auth():
    print("Testing /auth/register and /auth/login endpoints...")
    username = f"user_{int(time.time())}"
    email = f"{username}@example.com"
    password = "SecurePassword123!"

    # 1. Register
    reg_res = requests.post(
        f"{BASE_URL}/auth/register",
        json={"username": username, "email": email, "password": password}
    )
    print(f"Register status: {reg_res.status_code}")
    print(f"Register body: {reg_res.json()}")
    assert reg_res.status_code == 201, "Registration failed"
    token = reg_res.json()["access_token"]
    api_key = reg_res.json()["user"]["api_key"]

    # 2. Login
    login_res = requests.post(
        f"{BASE_URL}/auth/login",
        json={"username_or_email": username, "password": password}
    )
    print(f"Login status: {login_res.status_code}")
    print(f"Login body: {login_res.json()}")
    assert login_res.status_code == 200, "Login failed"

    # 3. /auth/me
    me_res = requests.get(
        f"{BASE_URL}/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    print(f"Me status: {me_res.status_code}")
    print(f"Me body: {me_res.json()}")
    assert me_res.status_code == 200, "Profile check failed"

    print("ALL AUTH TESTS PASSED PERFECTLY!")

if __name__ == "__main__":
    test_auth()
