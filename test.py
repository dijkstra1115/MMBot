import websocket
import json
import time
from datetime import datetime

# ==========================================
# ⚙️ 設定區
# ==========================================
WS_URL = "wss://perps.standx.com/ws-stream/v1"
SYMBOL = "BTC-USD"
CHANNEL = "depth_book"  # ✅ 終於找到正確的名字了

class OrderBookMonitor:
    def __init__(self):
        self.ws_url = WS_URL
        self.bids = [] # 買單列表
        self.asks = [] # 賣單列表

    def _on_open(self, ws):
        print(f"✅ 連線成功！訂閱深度頻道: {CHANNEL}")
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
            
            # 確保是深度數據
            if raw.get("channel") == CHANNEL and "data" in raw:
                data = raw["data"]
                
                # StandX 的深度格式通常是 bids/asks 陣列
                # 格式範例: {"bids": [["95000", "0.5"], ...], "asks": ...}
                if "bids" in data: self.bids = data["bids"]
                if "asks" in data: self.asks = data["asks"]
                
                self.display_book()

        except Exception as e:
            print(f"解析錯誤: {e}")

    def display_book(self):
        # 清除畫面 (Windows用 cls, Mac/Linux用 clear)
        print("\033c", end="") 
        
        print(f"=== 📊 StandX 深度監控 ({datetime.now().strftime('%H:%M:%S')}) ===")
        
        # 1. 顯示賣單 (Asks) - 顯示價格最低的 5 筆 (賣一 ~ 賣五)
        # 注意：賣單要倒序顯示，這樣價格高的在上面
        print(f"{'賣方 (Sell)':<10} | {'價格 (Price)':<12} | {'數量 (Qty)':<10} | {'累計 (Total)'}")
        print("-" * 50)
        
        cumulative_qty = 0
        # 取前 5 檔，反向顯示
        top_asks = self.asks[:5][::-1] 
        
        for price, qty in top_asks:
            price = float(price)
            qty = float(qty)
            cumulative_qty += qty
            # 如果數量 > 1 顆 BTC，標記為大戶 🐳
            whale_mark = "🐳" if qty > 1.0 else "  "
            print(f"{whale_mark:<10} | {price:,.2f}     | {qty:.4f}     | {cumulative_qty:.4f}")

        print("-" * 50)
        
        # 計算價差
        if self.bids and self.asks:
            best_bid = float(self.bids[0][0])
            best_ask = float(self.asks[0][0])
            spread = best_ask - best_bid
            print(f"   ⚡ 價差 (Spread): {spread:.2f} U  (中價: {(best_bid+best_ask)/2:,.2f})")

        print("-" * 50)

        # 2. 顯示買單 (Bids) - 顯示價格最高的 5 筆 (買一 ~ 買五)
        print(f"{'買方 (Buy)':<10} | {'價格 (Price)':<12} | {'數量 (Qty)':<10} | {'累計 (Total)'}")
        
        cumulative_qty = 0
        for price, qty in self.bids[:5]:
            price = float(price)
            qty = float(qty)
            cumulative_qty += qty
            whale_mark = "🐳" if qty > 1.0 else "  "
            print(f"{whale_mark:<10} | {price:,.2f}     | {qty:.4f}     | {cumulative_qty:.4f}")
            
        print("=" * 50)

    def run(self):
        ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message
        )
        ws.run_forever()

if __name__ == "__main__":
    monitor = OrderBookMonitor()
    monitor.run()