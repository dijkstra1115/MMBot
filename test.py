import os
import time
import uuid
import json
import base64
import requests
import base58
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder
from dotenv import load_dotenv

# 載入 .env 設定
load_dotenv()

# ==========================================
# ⚙️ 設定區
# ==========================================
API_BASE_URL = "https://perps.standx.com"
API_KEY = os.getenv("API_KEY")  # 注意：你的 .env 變數名目前是用這個存 Token/API Key
PRIVATE_KEY_RAW = os.getenv("SIGNING_KEY")

# ==========================================
# 🔐 輔助函式
# ==========================================
def decode_private_key(key_string):
    """解碼私鑰 (支援 Base58/Base64/Hex)"""
    try:
        key_string = key_string.strip()
        # 嘗試 Base58
        try:
            decoded = base58.b58decode(key_string)
            if len(decoded) == 32: return decoded.hex()
        except: pass
        # 嘗試 Base64
        try:
            pad = len(key_string) % 4
            if pad: key_string += '=' * (4 - pad)
            decoded = base64.urlsafe_b64decode(key_string)
            if len(decoded) == 32: return decoded.hex()
        except: pass
        # 嘗試 Hex
        if key_string.startswith("0x"): key_string = key_string[2:]
        return key_string
    except: return None

# ==========================================
# 🚀 主程式
# ==========================================
def main():
    print("🔍 正在檢查環境變數...")
    
    if not API_KEY:
        print("❌ 錯誤: 找不到 STANDX_JWT_TOKEN (API Key)")
        return
    if not PRIVATE_KEY_RAW:
        print("❌ 錯誤: 找不到 STANDX_PRIVATE_KEY")
        return

    # 1. 準備私鑰
    priv_hex = decode_private_key(PRIVATE_KEY_RAW)
    if not priv_hex:
        print("❌ 私鑰格式錯誤")
        return
    signer = SigningKey(priv_hex, encoder=HexEncoder)
    print("✅ 私鑰載入成功")

    # 2. 準備請求數據
    timestamp = int(time.time() * 1000)
    # 這是 StandX 查詢餘額的標準路徑
    path = "/api/query_balance" 
    params = f"t={timestamp}"
    full_url = f"{API_BASE_URL}{path}?{params}"

    # 3. 產生簽名 (GET 請求 payload 通常為空字串)
    payload_to_sign = "" 
    
    protocol_version = "v1"
    request_uuid = str(uuid.uuid4())
    
    # 簽名訊息格式: v1,uuid,timestamp,payload
    sig_msg = f"{protocol_version},{request_uuid},{timestamp},{payload_to_sign}"
    signed = signer.sign(sig_msg.encode('utf-8'))
    signature = base64.b64encode(signed.signature).decode('utf-8')

    # 4. 建構 Headers (模擬 API Key 模式)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}", # 如果是 JWT 模式
        # 如果是純 API Key 模式，可能需要: "X-API-KEY": API_KEY
        
        # 簽名 Headers
        "x-request-sign-version": protocol_version,
        "x-request-id": request_uuid,
        "x-request-timestamp": str(timestamp),
        "x-request-signature": signature
    }

    print(f"\n📡 正在發送請求到: {full_url}")
    print(f"🔑 使用 Token 前10碼: {API_KEY[:10]}...")

    try:
        response = requests.get(full_url, headers=headers, timeout=5)
        print(f"📩 HTTP 狀態碼: {response.status_code}")
        
        try:
            data = response.json()
            print("\n🎉 API 回傳內容:")
            print(json.dumps(data, indent=2))
            
            # 嘗試解析餘額
            balance_info = None
            if 'free' in data: 
                balance_info = data
            elif 'data' in data and 'free' in data['data']:
                balance_info = data['data']
            elif 'result' in data and 'free' in data['result']:
                balance_info = data['result']
                
            if balance_info:
                print("\n💰 解析結果:")
                print(f"   可用餘額 (Free): {float(balance_info.get('free', 0)):,.2f}")
                print(f"   總權益 (Total): {float(balance_info.get('total', 0)):,.2f}")
            else:
                print("\n⚠️ 未找到餘額欄位，請確認回傳格式")
                
        except json.JSONDecodeError:
            print("❌ 回傳不是 JSON:", response.text)

    except Exception as e:
        print(f"❌ 請求失敗: {e}")

if __name__ == "__main__":
    main()