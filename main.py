import requests
import time
import json
import uuid
import base64
import os
import sys
import threading
import websocket  # 需安裝: pip install websocket-client
from datetime import datetime
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

# ==========================================
# ⚙️ 機器人設定區
# ==========================================

# 1. 帳戶資訊
JWT_TOKEN = "eyJhbGciOiJFUzI1NiIsImtpZCI6IlhnaEJQSVNuN0RQVHlMcWJtLUVHVkVhOU1lMFpwdU9iMk1Qc2gtbUFlencifQ.eyJhIjoiMHhGZWMzNWFGNDk2ZGEyMEUwZThlMTBjNEMyQjdiODQ0Yzk4OTkwOUJlIiwiYyI6ImJzYyIsIm4iOiJXU2JQYnVwaDFJZDU5NG1GcyIsImkiOiIyMDI2LTAxLTEzVDEwOjEwOjA3LjY0MFoiLCJzIjoiaUdTQUlPWDMzK1h2MFpWREZFQ1d5ZFVUcEJmVlo1NFZDWnNxUVpoWXNsUTk2VzQrZUFBRzArcXdWMUNaZ1YxeUltN1hHWXNBYmxJY3puVUFjMFZ1M0JzPSIsInIiOiJtVEdqdDNGZzlYUTJOcXNOWDNCbWhZbUQ5SkVhN2llbVJYOU1xYzZSTVpGIiwidyI6MiwiaWF0IjoxNzY4Mjk5MDE0LCJleHAiOjE3Njg5MDM4MTR9.ARLPN9ipwKZlcNADxa_rwWriKWfZ8cHd0pdpAzEWYmMpM21mxHKT0FM-9KvrcNNarvSpqqh_Xc7Bb6yTS-_zEw"
PRIVATE_KEY_HEX = "0x42562e1400e47223ba4fa1c52b16f7ac926abb5339b8c01b270243871e063d11"

# 2. 交易標的
SYMBOL = "BTC-USD"
BASE_URL = "https://perps.standx.com"

# 3. 策略參數
ORDER_QTY = "0.096"       # 掛單數量，我預設0.1btc你們自己改
TARGET_BPS = 7           # 預設掛單位置 (8 bps)
MIN_BPS = 6              # < 6 bps 撤單
MAX_BPS = 10             # > 10 bps 重掛

REFRESH_RATE = 0.2       # 刷新頻率 (秒)
# ==========================================
# 📡 WebSocket 即時深度獲取
# ==========================================
class DepthListener:
    def __init__(self):
        self.bid = 0.0
        self.ask = 0.0
        self.mid_price = 0.0
        self.last_price = 0.0
        self.ready = False
        self.ws_url = "wss://perps.standx.com/ws-stream/v1"
        self.ws = None
        self.thread = threading.Thread(target=self._run_ws, daemon=True)
        self.thread.start()

    def _on_open(self, ws):
        print("✅ 即時監控買賣單數據Procyons版本連線 (Price Channel)...")
        subscribe_msg = {
            "subscribe": {
                "channel": "price", 
                "symbol": SYMBOL
            }
        }
        ws.send(json.dumps(subscribe_msg))

    def _on_message(self, ws, message):
        try:
            raw_data = json.loads(message)
            if raw_data.get("channel") == "price" and "data" in raw_data:
                data = raw_data["data"]
                if "spread" in data and len(data["spread"]) >= 2:
                    self.bid = float(data["spread"][0])
                    self.ask = float(data["spread"][1])
                if "mid_price" in data:
                    self.mid_price = float(data["mid_price"])
                if "last_price" in data:
                    self.last_price = float(data["last_price"])
                if self.bid > 0 and self.ask > 0:
                    self.ready = True
        except: pass

    def _on_error(self, ws, error):
        print(f"⚠️ WebSocket 錯誤: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        time.sleep(5)
        self._run_ws()

    def _run_ws(self):
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close
        )
        self.ws.run_forever()

    def get_price_basis(self):
        if self.ready:
            if self.mid_price > 0: return self.mid_price
            elif self.bid > 0 and self.ask > 0: return (self.bid + self.ask) / 2
            elif self.last_price > 0: return self.last_price
        return None

# ==========================================
# 🔐 交易 API
# ==========================================

class StandXBot:
    def __init__(self, token, private_key_hex):
        self.base_url = BASE_URL
        self.token = token
        if private_key_hex.startswith("0x"):
            private_key_hex = private_key_hex[2:]
        self.signing_key = SigningKey(private_key_hex, encoder=HexEncoder)
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        })
        self.depth = DepthListener()

    def _get_signed_headers(self, payload_str):
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

    def get_fallback_price(self):
        try:
            res = self.session.get(f"{self.base_url}/api/query_symbol_price?symbol={SYMBOL}", timeout=2)
            data = res.json()
            if 'last_price' in data: return float(data['last_price'])
        except: pass
        return None

    def get_open_orders(self):
        try:
            res = self.session.get(f"{self.base_url}/api/query_open_orders?symbol={SYMBOL}", timeout=2)
            data = res.json()
            if 'result' in data: return data['result']
        except: pass
        return []

    def get_position(self):
        try:
            
            ts = int(time.time() * 1000)
            res = self.session.get(f"{self.base_url}/api/query_positions?symbol={SYMBOL}&t={ts}", timeout=2)
            data = res.json()
            
            
            if isinstance(data, list):
                if len(data) > 0: return data[0]
            elif isinstance(data, dict) and 'result' in data:
                if isinstance(data['result'], list) and len(data['result']) > 0:
                    return data['result'][0]
            elif isinstance(data, dict) and 'data' in data:
                if isinstance(data['data'], list) and len(data['data']) > 0:
                    return data['data'][0]
        except Exception as e:
            print(f"[Debug] 抓持倉失敗: {e}")
        return None

    def place_order(self, side, price):
        endpoint = "/api/new_order"
        payload = {
            "symbol": SYMBOL,
            "side": side,
            "order_type": "limit",
            "qty": ORDER_QTY,
            "price": f"{price:.2f}",
            "time_in_force": "gtc",
            "reduce_only": False
        }
        payload_str = json.dumps(payload)
        try:
            res = self.session.post(self.base_url + endpoint, data=payload_str, headers=self._get_signed_headers(payload_str), timeout=1)
            return res.json()
        except: return {}

    def cancel_order(self, order_id):
        endpoint = "/api/cancel_order"
        payload = {"order_id": order_id}
        payload_str = json.dumps(payload)
        try:
            self.session.post(self.base_url + endpoint, data=payload_str, headers=self._get_signed_headers(payload_str), timeout=1)
        except: pass

    def market_close(self, side, qty):
        endpoint = "/api/new_order"
        
        qty_str = str(abs(float(qty)))
        payload = {
            "symbol": SYMBOL,
            "side": side,
            "order_type": "market",
            "qty": qty_str,
            "time_in_force": "ioc",
            "reduce_only": True
        }
        payload_str = json.dumps(payload)
        try:
            print(f"🔥 糟糕了有單，發送市價平倉單: {side} {qty_str}")
            res = self.session.post(self.base_url + endpoint, data=payload_str, headers=self._get_signed_headers(payload_str), timeout=2)
            print(f"   => 結果: {res.json()}")
        except Exception as e:
            print(f"   => 平倉請求失敗: {e}")

# ==========================================
# 🧠 策略主程序
# ==========================================

def run_strategy():
    if "請填入" in JWT_TOKEN:
        print("❌ 請打開腳本填入正確的 Token 和私鑰！")
        return

    bot = StandXBot(JWT_TOKEN, PRIVATE_KEY_HEX)
    print("🚀 盜用狗會破產，我說真的...")
    time.sleep(2) 

    while True:
        try:
            actions_log = []
            
            # 1. 優先檢查持倉
            position = bot.get_position()
            
            # 判斷是否有持倉
            has_position = False
            raw_qty = 0.0
            
            if position:
                # 嘗試讀取 qty，如果讀不到就給 0
                raw_qty = float(position.get('qty', 0))
                if raw_qty != 0:
                    has_position = True

            if has_position:
                print(f"🚨🚨🚨 完蛋啦！吃到單 qty={raw_qty}，平倉閃人中！ 🚨🚨🚨")
                
                # 1. 先撤所有單
                open_orders = bot.get_open_orders()
                for o in open_orders: bot.cancel_order(o['id'])
                
                # 2. 決定平倉方向 (不依賴 side 欄位，直接多空都平倉)
                # 邏輯：數量 > 0 是多單(Long) -> 要賣(Sell)
                #       數量 < 0 是空單(Short) -> 要買(Buy)
                if raw_qty > 0:
                    close_side = 'sell'
                else:
                    close_side = 'buy'
                
                # 3. 執行平倉 (取絕對值數量)
                bot.market_close(close_side, abs(raw_qty))
                
                # 4. 暫停一下檢查時間，避免 API 頻率限制
                time.sleep(0.5)
                continue

            # 2. 獲取基準價格
            mid_price = bot.depth.get_price_basis()
            price_source = "WS-Price"
            if mid_price is None:
                mid_price = bot.get_fallback_price()
                price_source = "HTTP"
            
            if mid_price is None:
                print("❌ 無法獲取價格...")
                time.sleep(1)
                continue

            # 3. 計算目標
            bps_decimal = TARGET_BPS / 10000
            target_buy = mid_price * (1 - bps_decimal)
            target_sell = mid_price * (1 + bps_decimal)

            # 4. 監控與補單
            open_orders = bot.get_open_orders()
            active_buy = False
            active_sell = False

            for order in open_orders:
                oid = order['id']
                oprice = float(order['price'])
                oside = order['side']
                diff_bps = abs(mid_price - oprice) / mid_price * 10000
                
                if diff_bps < MIN_BPS or diff_bps > MAX_BPS:
                    bot.cancel_order(oid)
                    actions_log.append(f"⚠️ {oside} 偏離 {diff_bps:.1f}bps -> 撤單")
                else:
                    if oside == 'buy': active_buy = True
                    if oside == 'sell': active_sell = True

            if not active_buy:
                res = bot.place_order('buy', target_buy)
                if 'code' in res and res['code'] == 0:
                    actions_log.append(f"✅ 掛買單 @ {target_buy:.2f}")
            
            if not active_sell:
                res = bot.place_order('sell', target_sell)
                if 'code' in res and res['code'] == 0:
                    actions_log.append(f"✅ 掛賣單 @ {target_sell:.2f}")

            # 5. 介面
            os.system('cls') # Windows 請改 cls
            print(f"=== 🛡️ Procyons-StandxMM巧克力策略（挖礦躺分） ===")
            print(f"⏰台灣時間現在： {datetime.now().strftime('%H:%M:%S')}")
            print(f"📊 即時價格: {mid_price:,.2f} ({price_source})")
            if bot.depth.ready:
                print(f"🟢 買方單: {bot.depth.bid:,.2f} 🔴 賣方單: {bot.depth.ask:,.2f}")
            print(f"🛡️ 現在持倉:(0) 非常的安全不要緊張 ")
            print("-" * 40)
            if not open_orders: print(" (無掛單，正在補單...)")
            for o in open_orders:
                d_bps = abs(mid_price - float(o['price'])) / mid_price * 10000
                print(f" [{o['side'].upper()}] {o['price']} (距 {d_bps:.1f}bps)")
            print("-" * 40)
            for log in actions_log: print(log)

        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(REFRESH_RATE)

if __name__ == "__main__":
    run_strategy()