import websocket
import json
import time
import threading

# ==========================================
# 🔑 請填入 Token
# ==========================================
JWT_TOKEN = "eyJhbGciOiJFUzI1NiIsImtpZCI6IlhnaEJQSVNuN0RQVHlMcWJtLUVHVkVhOU1lMFpwdU9iMk1Qc2gtbUFlencifQ.eyJhIjoiMHhGZWMzNWFGNDk2ZGEyMEUwZThlMTBjNEMyQjdiODQ0Yzk4OTkwOUJlIiwiYyI6ImJzYyIsIm4iOiJrS3VHcXhGUGdBdGxBSTZobCIsImkiOiIyMDI2LTAxLTE1VDAyOjI4OjI0LjYyMloiLCJzIjoiT1VVWTJRaUJ6UjJQcVlWazVOMzJQK1crWHZCWUtDMGpCRkN1Zmt2NVlWazBPVC9pZitMdGNTejdNMjV6VDNXaE9aODlBWmN0bEp3bG5vL3o1OEx4bnhzPSIsInIiOiJCWXd4dkFZbWRTVEdNVVE3NU5wbWRpb2Iyajl0VXdNQ3RtOWFZakI2OFE3RyIsInciOjIsImlhdCI6MTc2ODQ0NDExNywiZXhwIjoxNzY5MDQ4OTE3fQ.jSQPPbwQ86YlXkVxdX30fYv0UBM8TdBrLPXgpzx087YEhQ9qdiqJF2cgeAROrodFhV1tvPDNbryZVKxUyc4HOg"

WS_URL = "wss://perps.standx.com/ws-stream/v1"

def on_open(ws):
    print("✅ WebSocket 連線成功！")
    
    # 步驟 1: 先發送 Auth (確保身份驗證)
    # 我們只放 position，因為這是我們確定有效的
    auth_payload = {
        "auth": {
            "token": JWT_TOKEN,
            "streams": [{"channel": "position"}] 
        }
    }
    ws.send(json.dumps(auth_payload))
    print("🔐 1. Auth 請求已發送...")

    # 步驟 2: 發送獨立的 Balance 訂閱 (根據官方文件)
    # 稍微停頓一下確保 Auth 先被處理 (雖然通常可以用 Pipeline，但安全起見)
    time.sleep(0.5) 
    
    sub_payload = {
        "subscribe": {
            "channel": "balance"
        }
    }
    ws.send(json.dumps(sub_payload))
    print("📨 2. Balance 訂閱請求已發送！")
    print("=" * 60)

def on_message(ws, message):
    try:
        raw = json.loads(message)
        channel = raw.get("channel")

        # 顯示驗證結果
        if channel == "auth":
            print(f"🔑 驗證回應: {raw}")
            return

        # 顯示任何收到的數據
        if channel == "balance":
            print(f"\n🎉🎉🎉 成功抓到餘額了！ 🎉🎉🎉")
            print(json.dumps(raw, indent=2))
            
            # 解析並顯示關鍵數據
            data = raw.get("data", {})
            free = float(data.get("free", 0))
            total = float(data.get("total", 0))
            print(f"\n💰 可用餘額 (Free): {free:,.2f} DUSD")
            print(f"💰 總權益 (Total): {total:,.2f} DUSD")
            print("=" * 60)
            
        elif channel == "position":
            print(f"📦 收到 Position 更新")
            
        else:
            # 顯示其他雜訊 (如果是錯誤訊息)
            if "code" in raw and raw["code"] != 0:
                print(f"❌ 錯誤: {raw}")

    except Exception as e:
        print(f"Error: {e}")

def on_error(ws, error):
    print(f"⚠️ 錯誤: {error}")

def on_close(ws, status, msg):
    print("❌ 連線關閉")

if __name__ == "__main__":
    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()