import requests
import time
import json
import uuid
import base64
import binascii
import os
import sys
import threading
import websocket  # 需安裝: pip install websocket-client
import math
from datetime import datetime, timedelta
from collections import deque  # [新增] 用於儲存歷史價格
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from dotenv import load_dotenv
load_dotenv()

# ==========================================
# ⚙️ 機器人設定區
# ==========================================

# 1. 帳戶資訊
JWT_TOKEN = os.getenv("JWT_TOKEN") # 這個是你的JWT TOKEN，剛剛從瀏覽器抓取的 eyJ 開頭的那串

# 方式一：直接貼上 d 值 (base64 格式，43 個字)
D_VALUE_BASE64 = os.getenv("D_VALUE_BASE64") # 這個是你的d值，剛剛從瀏覽器抓取的

# 方式二：或直接使用 hex 格式的私鑰 (有貼 d 值就不用貼這個)
PRIVATE_KEY_HEX = os.getenv("PRIVATE_KEY_HEX")

# 2. 交易標的
SYMBOL = "BTC-USD"
BASE_URL = "https://perps.standx.com"

# 3. 策略參數
ORDER_QTY = "0.09"      # 掛單數量
TARGET_BPS = 8          # 預設掛單位置 (8 bps)
MIN_BPS = 7             # < 7 bps 撤單
MAX_BPS = 10            # > 10 bps 重掛

# 4. [修改] 波動保護參數
MAX_SAFE_SPREAD = 25      # 價差 > 25 bps 強制撤單
MAX_TREND_10S = 0.001     # [新增] 10秒內波動 > 0.1% (0.001)
MAX_TREND_20S = 0.0015    # [新增] 20秒內波動 > 0.15% (0.0015)
VOLATILITY_COOLDOWN = 300 # 觸發保護後的冷靜期 (秒) = 5分鐘

# 5. [新增] OBI (Order Book Imbalance) 參數
OBI_THRESHOLD = 0.6       # OBI 閾值 (0.6 表示買賣盤不平衡度 > 60%)
OBI_COOLDOWN = 60         # OBI 觸發後的暫停時間 (秒)
OBI_BPS_RANGE = 10        # OBI 計算範圍 (±10 bps = ±0.1%)

REFRESH_RATE = 0.2        # 刷新頻率 (秒)

# ==========================================
# 🔑 d 值轉換函數
# ==========================================

def convert_d_to_hex(d_val):
    """將 base64 格式的 d 值轉換為 hex 格式的私鑰"""
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
        self.ws_depth = None
        # [新增] 深度數據
        self.bids = []
        self.asks = []
        self.depth_ready = False
        self.thread = threading.Thread(target=self._run_ws, daemon=True)
        self.thread.start()
        # [新增] 深度 WebSocket 線程
        self.thread_depth = threading.Thread(target=self._run_ws_depth, daemon=True)
        self.thread_depth.start()

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

    def _on_open_depth(self, ws):
        subscribe_msg = {
            "subscribe": {
                "channel": "depth_book",
                "symbol": SYMBOL
            }
        }
        ws.send(json.dumps(subscribe_msg))

    def _on_message_depth(self, ws, message):
        try:
            raw_data = json.loads(message)
            if raw_data.get("channel") == "depth_book" and "data" in raw_data:
                data = raw_data["data"]
                if "bids" in data:
                    self.bids = data["bids"]
                if "asks" in data:
                    self.asks = data["asks"]
                if self.bids and self.asks:
                    self.depth_ready = True
        except: pass

    def _on_error_depth(self, ws, error):
        pass

    def _on_close_depth(self, ws, close_status_code, close_msg):
        time.sleep(5)
        self._run_ws_depth()

    def _run_ws_depth(self):
        self.ws_depth = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open_depth,
            on_message=self._on_message_depth,
            on_error=self._on_error_depth,
            on_close=self._on_close_depth
        )
        self.ws_depth.run_forever()

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

    def calculate_obi(self, mid_price):
        """
        計算 OBI (Order Book Imbalance) 指標
        計算 ±0.1% (10 bps) 範圍內的買賣盤總量不平衡度
        
        OBI = (買盤總量 - 賣盤總量) / (買盤總量 + 賣盤總量)
        範圍: -1 到 1，正數表示買盤多，負數表示賣盤多
        """
        if not self.depth_ready or not mid_price or mid_price == 0:
            return None
        
        # 計算價格範圍 (±0.1% = 10 bps)
        price_range_pct = OBI_BPS_RANGE / 10000.0
        lower_bound = mid_price * (1 - price_range_pct)  # 買盤上限
        upper_bound = mid_price * (1 + price_range_pct)  # 賣盤下限
        
        # 計算買盤總量 (價格在 [lower_bound, mid_price] 範圍內)
        bid_total = 0.0
        for price_str, qty_str in self.bids:
            price = float(price_str)
            if price >= lower_bound and price <= mid_price:
                bid_total += float(qty_str)
        
        # 計算賣盤總量 (價格在 [mid_price, upper_bound] 範圍內)
        ask_total = 0.0
        for price_str, qty_str in self.asks:
            price = float(price_str)
            if price >= mid_price and price <= upper_bound:
                ask_total += float(qty_str)
        
        # 計算 OBI
        total_volume = bid_total + ask_total
        if total_volume == 0:
            return None
        
        obi = (bid_total - ask_total) / total_volume
        return obi

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
            "price": f"{int(price)}",
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
    if not JWT_TOKEN:
        print("❌ 請設置 JWT_TOKEN！")
        return

    # 處理私鑰：優先使用 d 值轉換，否則使用直接提供的 hex
    private_key_hex = None
    if D_VALUE_BASE64:
        print("🔑 偵測到 d 值，正在轉換...")
        private_key_hex = convert_d_to_hex(D_VALUE_BASE64)
        if not private_key_hex:
            print("❌ d 值轉換失敗，請檢查格式！")
            return
        print("✅ d 值轉換成功！")
    elif PRIVATE_KEY_HEX:
        private_key_hex = PRIVATE_KEY_HEX
    else:
        print("❌ 請設置 D_VALUE_BASE64 或 PRIVATE_KEY_HEX！")
        return

    bot = StandXBot(JWT_TOKEN, private_key_hex)
    print("🚀 盜用狗會破產，我說真的 (10s/20s 趨勢保護版)...")
    time.sleep(2) 
    
    resume_time = datetime.min # 初始化恢復時間
    
    # [新增] 用來儲存價格
    price_history = deque()

    while True:
        try:
            actions_log = []
            
            # 1. 優先檢查持倉
            position = bot.get_position()
            
            has_position = False
            raw_qty = 0.0
            
            if position:
                raw_qty = float(position.get('qty', 0))
                if raw_qty != 0:
                    has_position = True

            if has_position:
                print(f"🚨🚨🚨 完蛋啦！吃到單 qty={raw_qty}，平倉閃人中！ 🚨🚨🚨")
                
                # 1. 先撤所有單
                open_orders = bot.get_open_orders()
                for o in open_orders: bot.cancel_order(o['id'])
                
                # 2. 決定平倉方向
                if raw_qty > 0:
                    close_side = 'sell'
                else:
                    close_side = 'buy'
                
                # 3. 執行平倉
                bot.market_close(close_side, abs(raw_qty))
                
                # 4. 暫停一下
                time.sleep(0.5)
                continue

            # 2. 獲取基準價格
            mid_price = bot.depth.get_price_basis()
            price_source = "WS-Price"
            if mid_price is None:
                mid_price = bot.get_fallback_price()
                price_source = "HTTP"
            
            if mid_price is None or mid_price == 0:
                print("❌ 無法獲取價格...")
                time.sleep(1)
                continue

            # ==========================================
            # [修改區] 波動保護冷靜期
            # ==========================================
            
            # A. 檢查是否在冷靜期 (Cooldown Check)
            if datetime.now() < resume_time:
                remaining = int((resume_time - datetime.now()).total_seconds())
                price_history.clear() # 清空歷史，避免數據滯後
                
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"=== ❄️ 市場趨勢過大，進入冷靜期 ❄️ ===")
                print(f"⏰ 剩餘時間: {remaining // 60}分 {remaining % 60}秒")
                print(f"📊 目前價格: {int(mid_price):,}")
                print(f"🛡️ 暫停掛單中，等待行情穩定...")
                time.sleep(1)
                continue

            # B. 檢查趨勢波動 (10秒 與 20秒)
            current_ts = time.time()
            price_history.append((current_ts, mid_price))

            # 清除超過 20 秒的舊資料
            while price_history and price_history[0][0] < current_ts - 20:
                price_history.popleft()

            # 計算變化率
            trend_10s_pct = 0.0
            trend_20s_pct = 0.0
            
            if price_history:
                # 1. 計算 20秒變化
                price_20s_ago = price_history[0][1]
                trend_20s_pct = abs(mid_price - price_20s_ago) / price_20s_ago
                
                # 2. 計算 10秒變化
                cutoff_10s = current_ts - 10
                price_10s_ago = mid_price # 預設為當前價格
                for t, p in price_history:
                    if t >= cutoff_10s:
                        price_10s_ago = p
                        break
                trend_10s_pct = abs(mid_price - price_10s_ago) / price_10s_ago

            # C. 檢查價差 Spread
            current_spread_bps = 0.0
            if bot.depth.ready and bot.depth.ask > bot.depth.bid:
                current_spread_bps = (bot.depth.ask - bot.depth.bid) / mid_price * 10000

            # D. 檢查 OBI (Order Book Imbalance)
            obi_value = bot.depth.calculate_obi(mid_price)
            obi_abs = abs(obi_value) if obi_value is not None else 0.0
            
            # E. 觸發條件判斷
            is_volatile = False
            reason = ""

            # 條件: OBI 不平衡 OR 價差大 OR 10秒變動>0.1% OR 20秒變動>0.15%
            if obi_value is not None and obi_abs > OBI_THRESHOLD:
                is_volatile = True
                reason = f"OBI不平衡 ({obi_value*100:.1f}%, 閾值{OBI_THRESHOLD*100:.0f}%)"
                cooldown_seconds = OBI_COOLDOWN
            elif current_spread_bps > MAX_SAFE_SPREAD:
                is_volatile = True
                reason = f"Spread價差過大 ({current_spread_bps:.1f}bps)"
                cooldown_seconds = VOLATILITY_COOLDOWN
            elif trend_10s_pct > MAX_TREND_10S: 
                is_volatile = True
                reason = f"10秒趨勢劇烈 ({trend_10s_pct*100:.2f}%)"
                cooldown_seconds = VOLATILITY_COOLDOWN
            elif trend_20s_pct > MAX_TREND_20S:
                is_volatile = True
                reason = f"20秒趨勢劇烈 ({trend_20s_pct*100:.2f}%)"
                cooldown_seconds = VOLATILITY_COOLDOWN

            if is_volatile:
                print(f"🌊 偵測到危險行情! 原因: {reason}")
                if obi_value is not None and obi_abs > OBI_THRESHOLD:
                    print(f"🛡️ 撤銷所有訂單並暫停交易 {cooldown_seconds} 秒...")
                else:
                    print(f"🛡️ 撤銷所有訂單並暫停交易 {cooldown_seconds//60} 分鐘...")
                open_orders = bot.get_open_orders()
                for o in open_orders: bot.cancel_order(o['id'])
                resume_time = datetime.now() + timedelta(seconds=cooldown_seconds)
                time.sleep(1)
                continue
            
            # ==========================================
            # 3. 計算目標
            bps_decimal = TARGET_BPS / 10000
            target_buy = math.floor(mid_price * (1 - bps_decimal))
            target_sell = math.ceil(mid_price * (1 + bps_decimal))

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
                    actions_log.append(f"✅ 掛買單 @ {int(target_buy)}")
            
            if not active_sell:
                res = bot.place_order('sell', target_sell) 
                if 'code' in res and res['code'] == 0:
                    actions_log.append(f"✅ 掛賣單 @ {int(target_sell)}")

            # 5. 介面
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"=== 🛡️ Procyons-StandxMM巧克力策略（挖礦躺分） ===")
            print(f"⏰台灣時間現在： {datetime.now().strftime('%H:%M:%S')}")
            print(f"📊 即時價格: {int(mid_price):,} ({price_source}) [Spread: {current_spread_bps:.1f}bps]")
            print(f"📈 10秒波動: {trend_10s_pct*100:.3f}% (限{MAX_TREND_10S*100}%)")
            print(f"📈 20秒波動: {trend_20s_pct*100:.3f}% (限{MAX_TREND_20S*100}%)")
            if obi_value is not None:
                obi_status = "🟢" if obi_abs <= OBI_THRESHOLD else "🔴"
                if abs(obi_value) < 0.01:
                    obi_direction = "平衡"
                elif obi_value > 0:
                    obi_direction = "買盤多"
                else:
                    obi_direction = "賣盤多"
                print(f"📊 OBI指標: {obi_status} {obi_value*100:.1f}% ({obi_direction}, 閾值{OBI_THRESHOLD*100:.0f}%)")
            else:
                print(f"📊 OBI指標: ⚠️ 數據未就緒")
            if bot.depth.ready:
                print(f"🟢 買方單: {int(bot.depth.bid):,} 🔴 賣方單: {int(bot.depth.ask):,}")
            print(f"🛡️ 現在持倉:(0) 非常的安全不要緊張 ")
            print("-" * 40)
            if not open_orders: print(" (無掛單，正在補單...)")
            for o in open_orders:
                d_bps = abs(mid_price - float(o['price'])) / mid_price * 10000
                print(f" [{o['side'].upper()}] {int(float(o['price']))} (距 {d_bps:.1f}bps)")
            print("-" * 40)
            for log in actions_log: print(log)

        except KeyboardInterrupt:
            print("\n\n🛑 收到退出信號 (Ctrl+C)，正在安全退出...")
            print("📋 正在撤銷所有掛單...")
            try:
                open_orders = bot.get_open_orders()
                for o in open_orders:
                    bot.cancel_order(o['id'])
                    print(f"   ✅ 已撤銷訂單: {o['id']}")
            except Exception as e:
                print(f"   ⚠️ 撤單時發生錯誤: {e}")
            print("👋 再見！")
            break
        except Exception as e:
            print(f"Error: {e}")
        
        time.sleep(REFRESH_RATE)

if __name__ == "__main__":
    try:
        run_strategy()
    except KeyboardInterrupt:
        print("\n\n🛑 收到退出信號 (Ctrl+C)，程序已退出")
        sys.exit(0)