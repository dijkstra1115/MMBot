import requests
import time
import json
import uuid
import base64
import binascii
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

# ==========================================
# 🟢 請填寫這裡
# ==========================================

# 1. 填入你抓到的 JWT
JWT_TOKEN = "" 

# 2. 填入你抓到的 'd' 值 (那串 43 個字的亂碼)
D_VALUE_BASE64 = "" 

# ==========================================
# 驗證邏輯
# ==========================================

def convert_d_to_hex(d_val):
    try:
        missing_padding = len(d_val) % 4
        if missing_padding:
            d_val += '=' * (4 - missing_padding)
        raw_bytes = base64.urlsafe_b64decode(d_val)
        hex_key = "0x" + binascii.hexlify(raw_bytes).decode('utf-8')
        return hex_key
    except Exception as e:
        print(f"❌ d 值轉換失敗: {e}")
        return None

class StandXVerifier:
    def __init__(self, token, private_key_hex):
        self.base_url = "https://perps.standx.com"
        self.token = token
        if private_key_hex.startswith("0x"):
            private_key_hex = private_key_hex[2:]
        try:
            self.signing_key = SigningKey(private_key_hex, encoder=HexEncoder)
        except Exception as e:
            print(f"❌ 私鑰格式錯誤: {e}")
            self.signing_key = None

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        })

    def _get_signed_headers(self, payload_str):
        if not self.signing_key: return {}
        req_id = str(uuid.uuid4())
        timestamp = int(time.time() * 1000)
        version = "v1"
        msg = f"{version},{req_id},{timestamp},{payload_str}"
        signed = self.signing_key.sign(msg.encode('utf-8'))
        signature_b64 = base64.b64encode(signed.signature).decode('utf-8')
        return {
            "x-request-sign-version": version,
            "x-request-id": req_id,
            "x-request-timestamp": str(timestamp),
            "x-request-signature": signature_b64
        }

    def verify(self):
        print("📡 正在連接 StandX 伺服器驗證簽名...")
        endpoint = "/api/cancel_order"
        
        # 🔥 修改處：這裡把 "0" 改成了 0 (數字)
        payload = {"order_id": 0} 
        
        payload_str = json.dumps(payload)
        
        try:
            headers = self._get_signed_headers(payload_str)
            res = self.session.post(
                self.base_url + endpoint, 
                data=payload_str, 
                headers=headers, 
                timeout=5
            )
            
            # 判斷結果
            # 如果回傳 "order not found" 或 "Order does not exist"，代表簽名過了，只是單號不存在
            if res.status_code == 200 or "not found" in res.text.lower() or "does not exist" in res.text.lower():
                print("\n" + "="*40)
                print("✅ 驗證大成功！Private Key 正確無誤！")
                print("="*40)
                print(f"伺服器回應: {res.text}")
                return True

            elif res.status_code == 401:
                print("\n❌ 驗證失敗: JWT Token 過期 (401)")
            
            elif "signature" in res.text.lower() or "forbidden" in res.text.lower():
                print("\n❌ 驗證失敗: Private Key 錯誤 (簽名無效)")
                print(f"回應: {res.text}")
            
            elif res.status_code == 422:
                print(f"\n❌ 格式還是不對: {res.text}")

            else:
                print(f"\n⚠️ 未知回應 (Code {res.status_code}): {res.text}")

        except Exception as e:
            print(f"❌ 連線錯誤: {e}")

if __name__ == "__main__":
    real_hex_key = convert_d_to_hex(D_VALUE_BASE64)
    if real_hex_key:
        print(f"🔑 轉換後的 Hex Key: {real_hex_key}")
        print("-" * 30)
        verifier = StandXVerifier(JWT_TOKEN, real_hex_key)
        verifier.verify()