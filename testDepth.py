import websocket
import json
import time
from datetime import datetime
import os

# ==========================================
# ⚙️ 設定區
# ==========================================
WS_URL = "wss://perps.standx.com/ws-stream/v1"
SYMBOL = "BTC-USD"
CHANNEL = "depth_book" 

# 🔥 設定你想看幾檔？ (建議 10~30，太多畫面會塞不下)
DISPLAY_LIMIT = 20 

class DeepBookMonitor:
    def __init__(self):
        self.ws_url = WS_URL
        self.bids = [] 
        self.asks = [] 

    def _on_open(self, ws):
        print(f"✅ 連線成功！正在訂閱深度: {CHANNEL}...")
        msg = {
            "subscribe": {
                "channel": CHANNEL,
                "symbol": SYMBOL
            }
        }
        ws.send(json.dumps(msg))

    def _on_message(self, ws, message):
        try:
            raw = json.loads(message)
            
            if raw.get("channel") == CHANNEL and "data" in raw:
                data = raw["data"]
                
                if "bids" in data: self.bids = data["bids"]
                if "asks" in data: self.asks = data["asks"]
                
                self.display_book()

        except Exception as e:
            # 忽略一些非 JSON 的雜訊
            pass

    def display_book(self):
        # 清除終端機畫面
        os.system('cls' if os.name == 'nt' else 'clear')
        
        # 1. 檢查我們到底收到了多少數據
        total_bids = len(self.bids)
        total_asks = len(self.asks)
        
        print(f"=== 🌊 StandX 深海探測雷達 ({datetime.now().strftime('%H:%M:%S')}) ===")
        print(f"📡 伺服器回傳總深度: 買單 {total_bids} 檔 | 賣單 {total_asks} 檔")
        print(f"👀 目前顯示範圍: 前 {DISPLAY_LIMIT} 檔")
        print("=" * 60)

        # 2. 顯示賣單 (Asks) - 倒序顯示 (價格高的在上面，價格低的在下面接近中價)
        print(f"{'賣方 (Sell)':<10} | {'價格 (Price)':<12} | {'數量 (Qty)':<10} | {'累計 (Total)'}")
        print("-" * 60)
        
        # 截取我們要看的範圍
        show_asks = self.asks[:DISPLAY_LIMIT]
        # 因為賣單要「價格低 -> 高」排列，但在終端機顯示時，我們希望「價格高」在上面
        # 所以要反轉列表，讓最優賣價 (賣一) 在最下面，緊貼著買單
        show_asks = show_asks[::-1] 
        
        cumulative_qty = sum(float(x[1]) for x in self.asks[:DISPLAY_LIMIT]) # 這是為了算倒序累計，簡化處理我們先不算反向累計，直接顯示單層

        # 為了讓累計看起來正確，我們應該從「賣一」往上算
        # 這裡做一個小技巧：先算好每一檔的累計，再反轉顯示
        ask_data_with_cum = []
        cum = 0
        for p, q in self.asks[:DISPLAY_LIMIT]:
            cum += float(q)
            ask_data_with_cum.append((float(p), float(q), cum))
        
        # 反轉準備顯示
        for p, q, c in ask_data_with_cum[::-1]:
            whale = "🚨大戶" if q > 2.0 else "  "  # 設定 > 2 顆 BTC 為大戶
            print(f"{whale:<10} | {p:,.2f}     | {q:.4f}     | {c:.4f}")

        # 3. 顯示價差 (Spread)
        if self.bids and self.asks:
            best_bid = float(self.bids[0][0])
            best_ask = float(self.asks[0][0])
            spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2
            print(f"{' ' * 24}⚡ 價差: {spread:.2f} | 中價: {mid:,.2f}")

        print("-" * 60)

        # 4. 顯示買單 (Bids) - 正序顯示 (價格高的在上面，價格低的在下面)
        print(f"{'買方 (Buy)':<10} | {'價格 (Price)':<12} | {'數量 (Qty)':<10} | {'累計 (Total)'}")
        
        cum = 0
        for p, q in self.bids[:DISPLAY_LIMIT]:
            p = float(p)
            q = float(q)
            cum += q
            whale = "🚨大戶" if q > 2.0 else "  "
            print(f"{whale:<10} | {p:,.2f}     | {q:.4f}     | {cum:.4f}")
            
        print("=" * 60)

    def run(self):
        ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message
        )
        ws.run_forever()

if __name__ == "__main__":
    monitor = DeepBookMonitor()
    try:
        monitor.run()
    except KeyboardInterrupt:
        print("程式結束")