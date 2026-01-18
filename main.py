import requests
import time
import json
import uuid
import base58
import base64
import binascii
import os
import sys
import threading
import websocket
import math
import signal
from datetime import datetime, timedelta
from collections import deque
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder
import logging

from dotenv import load_dotenv
load_dotenv()

# ==========================================
# 🛠️ 日誌系統配置
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

# ==========================================
# ⚙️ 交易配置參數
# ==========================================

# 身份認證
API_KEY = os.getenv("API_KEY")  # API 認證令牌，用於 API 身份驗證
SIGNING_KEY = os.getenv("SIGNING_KEY")  # Base58 編碼的私鑰，用於簽名交易請求

# 交易對設定
TRADING_PAIR = "BTC-USD"  # 交易對符號
API_BASE_URL = "https://perps.standx.com"  # API 基礎網址

# 做市策略配置
ORDER_SIZE = "0.226"  # 每筆訂單大小，要注意單位是 "幣", 500u 40x槓桿大概能開 0.09 (多空都開)
SPREAD_TARGET_BPS = 8  # 目標價差（基點），用於計算掛單價格
SPREAD_MIN_BPS = 7  # 最小價差（基點），低於此值會撤單
SPREAD_MAX_BPS = 10  # 最大價差（基點），超過此值會撤單

# 風險控制參數
SPREAD_DANGER_THRESHOLD = 25  # 價差危險閾值（基點），超過會觸發風控
VOLATILITY_SHORT_TERM_PCT = 0.001  # 10秒短期波動率上限（百分比）
VOLATILITY_MID_TERM_PCT = 0.0015  # 20秒中期波動率上限（百分比）
MARKET_PAUSE_DURATION = 300  # 市場波動觸發的暫停時間（秒）
POSITION_PAUSE_DURATION = 300  # 吃單後的冷靜期時間（秒）

# OBI 訂單簿不平衡參數
ORDERBOOK_IMBALANCE_LIMIT = 0.85  # 訂單簿不平衡閾值（0-1），超過會暫停交易
ORDERBOOK_PAUSE_DURATION = 180  # OBI 觸發的暫停時間（秒）
ORDERBOOK_PRICE_RANGE_BPS = 20  # 計算 OBI 的價格範圍（基點）

# 系統參數
LOOP_INTERVAL = 0.2  # 主循環間隔時間（秒）
PRICE_HISTORY_SIZE = 200  # 價格歷史記錄緩衝區大小

# 全域狀態變數
is_shutting_down = False
trading_bot = None

# ==========================================
# 🔐 密鑰轉換工具
# ==========================================

def decode_base58_private_key(b58_string):
    """將 Base58 編碼的私鑰轉換為十六進制格式（純十六進制字符串，不帶 0x 前綴）"""
    try:
        decoded_bytes = base58.b58decode(b58_string)
        hex_format = binascii.hexlify(decoded_bytes).decode('utf-8')
        return hex_format
    except Exception as err:
        log.error(f"Base58 密鑰解碼失敗: {err}")
        return None

# ==========================================
# 📊 市場數據監聽器
# ==========================================

class MarketDataStream:
    def __init__(self):
        # 價格數據
        self.current_bid = 0.0
        self.current_ask = 0.0
        self.market_mid_price = 0.0
        self.latest_trade_price = 0.0
        self.price_data_ready = False
        
        # 深度數據
        self.bid_levels = []
        self.ask_levels = []
        self.depth_data_ready = False
        
        # WebSocket 配置
        self.stream_url = "wss://perps.standx.com/ws-stream/v1"
        self.price_ws = None
        self.depth_ws = None
        
        # 連線管理
        self.data_lock = threading.Lock()
        self.retry_count = 0
        self.max_retries = 10
        self.last_data_timestamp = time.time()
        
        # 啟動 WebSocket 連線
        self.price_thread = threading.Thread(target=self._start_price_stream, daemon=True)
        self.price_thread.start()
        
        self.depth_thread = threading.Thread(target=self._start_depth_stream, daemon=True)
        self.depth_thread.start()
        
        self.health_monitor = threading.Thread(target=self._monitor_health, daemon=True)
        self.health_monitor.start()

    def _monitor_health(self):
        """監控連線健康狀態"""
        while True:
            try:
                time.sleep(30)
                elapsed = time.time() - self.last_data_timestamp
                if elapsed > 60:
                    log.warning(f"數據流異常: {int(elapsed)}秒 無新數據")
                    if self.price_ws:
                        log.info("重啟價格數據流...")
                        self.price_ws.close()
            except Exception as err:
                log.error(f"健康監控錯誤: {err}")

    def _handle_price_open(self, ws):
        log.info(f"價格頻道連線成功 (重試次數: {self.retry_count})")
        print("✅ 即時監控買賣單數據Procyons版本連線 (Price Channel)...")
        subscription = {
            "subscribe": {
                "channel": "price",
                "symbol": TRADING_PAIR
            }
        }
        ws.send(json.dumps(subscription))
        self.retry_count = 0

    def _handle_price_message(self, ws, raw_message):
        try:
            self.last_data_timestamp = time.time()
            parsed_data = json.loads(raw_message)
            if parsed_data.get("channel") == "price" and "data" in parsed_data:
                market_data = parsed_data["data"]
                
                with self.data_lock:
                    if "spread" in market_data and len(market_data["spread"]) >= 2:
                        self.current_bid = float(market_data["spread"][0])
                        self.current_ask = float(market_data["spread"][1])
                    if "mid_price" in market_data:
                        self.market_mid_price = float(market_data["mid_price"])
                    if "last_price" in market_data:
                        self.latest_trade_price = float(market_data["last_price"])
                    if self.current_bid > 0 and self.current_ask > 0:
                        self.price_data_ready = True
        except json.JSONDecodeError as err:
            log.error(f"JSON 解析錯誤: {err}")
        except Exception as err:
            log.error(f"價格訊息處理錯誤: {err}")

    def _handle_price_error(self, ws, error):
        log.error(f"價格 WebSocket 錯誤: {error}")

    def _handle_price_close(self, ws, status_code, close_reason):
        log.warning(f"價格連線關閉 (狀態: {status_code}, 原因: {close_reason})")
        
        if self.retry_count < self.max_retries:
            self.retry_count += 1
            backoff_time = min(5 * self.retry_count, 30)
            log.info(f"等待 {backoff_time}秒 後重連 (第 {self.retry_count} 次)")
            time.sleep(backoff_time)
            self._start_price_stream()
        else:
            log.critical(f"已達最大重連次數 ({self.max_retries})")

    def _start_price_stream(self):
        try:
            self.price_ws = websocket.WebSocketApp(
                self.stream_url,
                on_open=self._handle_price_open,
                on_message=self._handle_price_message,
                on_error=self._handle_price_error,
                on_close=self._handle_price_close
            )
            self.price_ws.run_forever()
        except Exception as err:
            log.error(f"價格流執行錯誤: {err}")

    def _handle_depth_open(self, ws):
        log.info("深度頻道連線成功")
        subscription = {
            "subscribe": {
                "channel": "depth_book",
                "symbol": TRADING_PAIR
            }
        }
        ws.send(json.dumps(subscription))

    def _handle_depth_message(self, ws, raw_message):
        try:
            parsed_data = json.loads(raw_message)
            if parsed_data.get("channel") == "depth_book" and "data" in parsed_data:
                depth_data = parsed_data["data"]
                with self.data_lock:
                    if "bids" in depth_data:
                        self.bid_levels = depth_data["bids"]
                    if "asks" in depth_data:
                        self.ask_levels = depth_data["asks"]
                    if self.bid_levels and self.ask_levels:
                        self.depth_data_ready = True
        except Exception as err:
            log.error(f"深度數據處理錯誤: {err}")

    def _handle_depth_error(self, ws, error):
        log.error(f"深度 WebSocket 錯誤: {error}")

    def _handle_depth_close(self, ws, status_code, close_reason):
        log.warning("深度連線關閉，5秒後重連")
        time.sleep(5)
        self._start_depth_stream()

    def _start_depth_stream(self):
        try:
            self.depth_ws = websocket.WebSocketApp(
                self.stream_url,
                on_open=self._handle_depth_open,
                on_message=self._handle_depth_message,
                on_error=self._handle_depth_error,
                on_close=self._handle_depth_close
            )
            self.depth_ws.run_forever()
        except Exception as err:
            log.error(f"深度流執行錯誤: {err}")

    def fetch_current_price(self):
        """獲取當前市場中間價"""
        with self.data_lock:
            if self.price_data_ready:
                if self.market_mid_price > 0:
                    return self.market_mid_price
                elif self.current_bid > 0 and self.current_ask > 0:
                    return (self.current_bid + self.current_ask) / 2
                elif self.latest_trade_price > 0:
                    return self.latest_trade_price
        return None

    def compute_orderbook_imbalance(self, reference_price):
        """
        計算訂單簿不平衡指標 (OBI)
        範圍: -1 到 1
        正值表示買盤強勢，負值表示賣盤強勢
        """
        if not self.depth_data_ready or not reference_price or reference_price == 0:
            return None
        
        with self.data_lock:
            # 計算價格範圍
            range_factor = ORDERBOOK_PRICE_RANGE_BPS / 10000.0
            lower_price_bound = reference_price * (1 - range_factor)
            upper_price_bound = reference_price * (1 + range_factor)
            
            # 統計買盤量
            total_bid_volume = 0.0
            for price_level, volume_str in self.bid_levels:
                try:
                    level_price = float(price_level)
                    if lower_price_bound <= level_price <= reference_price:
                        total_bid_volume += float(volume_str)
                except:
                    pass
            
            # 統計賣盤量
            total_ask_volume = 0.0
            for price_level, volume_str in self.ask_levels:
                try:
                    level_price = float(price_level)
                    if reference_price <= level_price <= upper_price_bound:
                        total_ask_volume += float(volume_str)
                except:
                    pass
            
            # 計算不平衡度
            combined_volume = total_bid_volume + total_ask_volume
            if combined_volume == 0:
                return None
            
            imbalance = (total_bid_volume - total_ask_volume) / combined_volume
            return imbalance
    
    def get_orderbook_depth(self, reference_price):
        """
        獲取 ORDERBOOK_PRICE_RANGE_BPS 範圍內的訂單簿深度
        返回: (買盤總量, 賣盤總量, 總深度)
        """
        if not self.depth_data_ready or not reference_price or reference_price == 0:
            return None, None, None
        
        with self.data_lock:
            # 計算價格範圍
            range_factor = ORDERBOOK_PRICE_RANGE_BPS / 10000.0
            lower_price_bound = reference_price * (1 - range_factor)
            upper_price_bound = reference_price * (1 + range_factor)
            
            # 統計買盤量
            total_bid_volume = 0.0
            for price_level, volume_str in self.bid_levels:
                try:
                    level_price = float(price_level)
                    if lower_price_bound <= level_price <= reference_price:
                        total_bid_volume += float(volume_str)
                except:
                    pass
            
            # 統計賣盤量
            total_ask_volume = 0.0
            for price_level, volume_str in self.ask_levels:
                try:
                    level_price = float(price_level)
                    if reference_price <= level_price <= upper_price_bound:
                        total_ask_volume += float(volume_str)
                except:
                    pass
            
            total_depth = total_bid_volume + total_ask_volume
            return total_bid_volume, total_ask_volume, total_depth
    
    def get_detailed_orderbook_depth(self, reference_price):
        """
        獲取 ORDERBOOK_PRICE_RANGE_BPS 範圍內每個價格檔位的詳細深度
        返回: {
            'bid_levels': [(價格, 深度), ...],  # 買盤檔位列表，按價格降序
            'ask_levels': [(價格, 深度), ...],  # 賣盤檔位列表，按價格升序
            'total_bid': 總買盤量,
            'total_ask': 總賣盤量,
            'total_depth': 總深度
        }
        """
        if not self.depth_data_ready or not reference_price or reference_price == 0:
            return None
        
        with self.data_lock:
            # 計算價格範圍
            range_factor = ORDERBOOK_PRICE_RANGE_BPS / 10000.0
            lower_price_bound = reference_price * (1 - range_factor)
            upper_price_bound = reference_price * (1 + range_factor)
            
            # 收集買盤檔位
            bid_levels = []
            total_bid_volume = 0.0
            for price_level, volume_str in self.bid_levels:
                try:
                    level_price = float(price_level)
                    if lower_price_bound <= level_price <= reference_price:
                        volume = float(volume_str)
                        bid_levels.append((level_price, volume))
                        total_bid_volume += volume
                except:
                    pass
            
            # 收集賣盤檔位
            ask_levels = []
            total_ask_volume = 0.0
            for price_level, volume_str in self.ask_levels:
                try:
                    level_price = float(price_level)
                    if reference_price <= level_price <= upper_price_bound:
                        volume = float(volume_str)
                        ask_levels.append((level_price, volume))
                        total_ask_volume += volume
                except:
                    pass
            
            # 排序：買盤按價格降序，賣盤按價格升序
            bid_levels.sort(key=lambda x: x[0], reverse=True)
            ask_levels.sort(key=lambda x: x[0])
            
            return {
                'bid_levels': bid_levels,
                'ask_levels': ask_levels,
                'total_bid': total_bid_volume,
                'total_ask': total_ask_volume,
                'total_depth': total_bid_volume + total_ask_volume
            }

# ==========================================
# 📝 成交記錄系統
# ==========================================

class TradeLogger:
    def __init__(self, log_filename="trades.log"):
        """
        初始化成交記錄器
        log_filename: 日誌文件名，默認為 trades.log
        """
        self.log_filename = log_filename
        self.log_lock = threading.Lock()
        # 確保日誌文件存在，如果不存在則創建並寫入標題
        self._initialize_log_file()
    
    def _initialize_log_file(self):
        """初始化日誌文件，寫入表頭"""
        try:
            if not os.path.exists(self.log_filename):
                with open(self.log_filename, 'w', encoding='utf-8') as f:
                    f.write("=" * 80 + "\n")
                    f.write("交易成交記錄日誌\n")
                    f.write("=" * 80 + "\n")
                    f.write(f"開始時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write("=" * 80 + "\n\n")
        except Exception as e:
            log.error(f"初始化日誌文件失敗: {e}")
    
    def log_trade(self, position_data, market_price=None, closing_info=None, 
                  orderbook_depth=None, detailed_orderbook=None, short_term_volatility=None, mid_term_volatility=None):
        """
        記錄成交信息
        
        Args:
            position_data: 持倉數據字典，包含 qty, side 等信息
            market_price: 市場價格（可選）
            closing_info: 平倉信息字典（可選），包含 close_time, close_price 等
                          如果提供，則追加平倉信息到最新記錄；否則創建新記錄
            orderbook_depth: 訂單簿深度元組 (買盤總量, 賣盤總量, 總深度) 或 None（已棄用，使用 detailed_orderbook）
            detailed_orderbook: 詳細訂單簿數據字典，包含每個價格檔位的深度
            short_term_volatility: 10秒波動率（百分比，例如 0.001 表示 0.1%）
            mid_term_volatility: 20秒波動率（百分比，例如 0.0015 表示 0.15%）
        """
        try:
            with self.log_lock:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # 解析持倉信息
                position_size = float(position_data.get('qty', 0))
                position_side = 'LONG' if position_size > 0 else 'SHORT'
                entry_price = position_data.get('entry_price', position_data.get('avg_price', 'N/A'))
                
                # 如果有平倉信息，追加到最新記錄
                if closing_info:
                    # 追加平倉信息到文件末尾
                    with open(self.log_filename, 'a', encoding='utf-8') as f:
                        f.write(f"平倉時間: {closing_info.get('close_time', 'N/A')}\n")
                        f.write(f"平倉價格: {closing_info.get('close_price', 'N/A')}\n")
                        if 'pnl' in closing_info:
                            f.write(f"損益: {closing_info['pnl']}\n")
                        f.write("-" * 80 + "\n\n")
                    
                    print(f"📝 平倉信息已更新到: {self.log_filename}")
                    log.info(f"平倉信息已更新到日誌文件")
                else:
                    # 創建新的成交記錄
                    log_entry = []
                    log_entry.append("-" * 80)
                    log_entry.append(f"成交時間: {timestamp}")
                    log_entry.append(f"持倉方向: {position_side}")
                    log_entry.append(f"持倉數量: {abs(position_size)}")
                    log_entry.append(f"開倉價格: {entry_price}")
                    
                    if market_price:
                        log_entry.append(f"市場價格: {market_price}")
                    
                    # 記錄訂單簿深度（詳細檔位）
                    if detailed_orderbook:
                        log_entry.append(f"訂單簿深度 (範圍: ±{ORDERBOOK_PRICE_RANGE_BPS}bps):")
                        log_entry.append(f"  總買盤: {detailed_orderbook['total_bid']:.4f}")
                        log_entry.append(f"  總賣盤: {detailed_orderbook['total_ask']:.4f}")
                        log_entry.append(f"  總深度: {detailed_orderbook['total_depth']:.4f}")
                        log_entry.append("")
                        
                        # 記錄買盤檔位（從高到低）
                        if detailed_orderbook['bid_levels']:
                            log_entry.append("  買盤檔位:")
                            for price, volume in detailed_orderbook['bid_levels']:
                                price_diff_bps = ((price - market_price) / market_price * 10000) if market_price else 0
                                log_entry.append(f"    {int(price):,} ({price_diff_bps:+.1f}bps) : {volume:.4f}")
                        else:
                            log_entry.append("  買盤檔位: 無")
                        
                        log_entry.append("")
                        
                        # 記錄賣盤檔位（從低到高）
                        if detailed_orderbook['ask_levels']:
                            log_entry.append("  賣盤檔位:")
                            for price, volume in detailed_orderbook['ask_levels']:
                                price_diff_bps = ((price - market_price) / market_price * 10000) if market_price else 0
                                log_entry.append(f"    {int(price):,} ({price_diff_bps:+.1f}bps) : {volume:.4f}")
                        else:
                            log_entry.append("  賣盤檔位: 無")
                    elif orderbook_depth and orderbook_depth[0] is not None:
                        # 兼容舊格式
                        bid_vol, ask_vol, total_vol = orderbook_depth
                        log_entry.append(f"訂單簿深度 (範圍: ±{ORDERBOOK_PRICE_RANGE_BPS}bps):")
                        log_entry.append(f"  買盤總量: {bid_vol:.4f}")
                        log_entry.append(f"  賣盤總量: {ask_vol:.4f}")
                        log_entry.append(f"  總深度: {total_vol:.4f}")
                    else:
                        log_entry.append(f"訂單簿深度: 數據未就緒")
                    
                    # 記錄波動率
                    if short_term_volatility is not None:
                        log_entry.append(f"10秒波動率: {short_term_volatility*100:.4f}%")
                    else:
                        log_entry.append(f"10秒波動率: 數據未就緒")
                    
                    if mid_term_volatility is not None:
                        log_entry.append(f"20秒波動率: {mid_term_volatility*100:.4f}%")
                    else:
                        log_entry.append(f"20秒波動率: 數據未就緒")
                    
                    log_entry.append("-" * 80)
                    log_entry.append("")
                    
                    # 寫入文件
                    with open(self.log_filename, 'a', encoding='utf-8') as f:
                        f.write('\n'.join(log_entry) + '\n')
                    
                    # 同時輸出到控制台
                    print(f"📝 成交記錄已寫入: {self.log_filename}")
                    log.info(f"成交記錄已寫入日誌文件: {position_side} {abs(position_size)} @ {entry_price}")
                
        except Exception as e:
            log.error(f"記錄成交日誌失敗: {e}")
            print(f"⚠️ 記錄成交日誌時發生錯誤: {e}")

# ==========================================
# 🤖 交易機器人核心
# ==========================================

class TradingBot:
    def __init__(self, auth_token, signing_key_hex):
        self.api_url = API_BASE_URL
        self.auth_token = auth_token
        
        # 處理私鑰格式
        self.signer = SigningKey(signing_key_hex, encoder=HexEncoder)
        
        # HTTP 會話
        self.http_session = requests.Session()
        self.http_session.headers.update({
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type": "application/json"
        })
        
        # 市場數據流
        self.market_stream = MarketDataStream()

    def _create_signature_headers(self, request_payload):
        """生成請求簽名標頭"""
        request_uuid = str(uuid.uuid4())
        current_timestamp = int(time.time() * 1000)
        protocol_version = "v1"
        
        signature_message = f"{protocol_version},{request_uuid},{current_timestamp},{request_payload}"
        signed_data = self.signer.sign(signature_message.encode('utf-8'))
        signature_encoded = base64.b64encode(signed_data.signature).decode('utf-8')
        
        return {
            "x-request-sign-version": protocol_version,
            "x-request-id": request_uuid,
            "x-request-timestamp": str(current_timestamp),
            "x-request-signature": signature_encoded
        }

    def fetch_backup_price(self):
        """備用價格獲取（HTTP API）"""
        try:
            response = self.http_session.get(
                f"{self.api_url}/api/query_symbol_price?symbol={TRADING_PAIR}",
                timeout=2
            )
            result = response.json()
            if 'last_price' in result:
                return float(result['last_price'])
        except requests.exceptions.Timeout:
            log.warning("HTTP 價格查詢超時")
        except Exception as err:
            log.error(f"價格查詢失敗: {err}")
        return None

    def query_active_orders(self):
        """查詢當前活躍訂單"""
        try:
            response = self.http_session.get(
                f"{self.api_url}/api/query_open_orders?symbol={TRADING_PAIR}",
                timeout=2
            )
            result = response.json()
            if 'result' in result:
                return result['result']
        except requests.exceptions.Timeout:
            log.warning("訂單查詢超時")
        except Exception as err:
            log.error(f"訂單查詢失敗: {err}")
        return []

    def query_current_position(self):
        """查詢當前持倉"""
        try:
            query_timestamp = int(time.time() * 1000)
            response = self.http_session.get(
                f"{self.api_url}/api/query_positions?symbol={TRADING_PAIR}&t={query_timestamp}",
                timeout=2
            )
            result = response.json()
            
            # 處理多種響應格式
            if isinstance(result, list) and len(result) > 0:
                return result[0]
            elif isinstance(result, dict):
                if 'result' in result and isinstance(result['result'], list):
                    if len(result['result']) > 0:
                        return result['result'][0]
                elif 'data' in result and isinstance(result['data'], list):
                    if len(result['data']) > 0:
                        return result['data'][0]
        except requests.exceptions.Timeout:
            log.warning("持倉查詢超時")
        except Exception as err:
            log.error(f"持倉查詢失敗: {err}")
        return None

    def submit_limit_order(self, order_side, order_price):
        """提交限價單"""
        api_endpoint = "/api/new_order"
        order_data = {
            "symbol": TRADING_PAIR,
            "side": order_side,
            "order_type": "limit",
            "qty": ORDER_SIZE,
            "price": f"{int(order_price)}",
            "time_in_force": "gtc",
            "reduce_only": False
        }
        payload_string = json.dumps(order_data)
        
        try:
            response = self.http_session.post(
                self.api_url + api_endpoint,
                data=payload_string,
                headers=self._create_signature_headers(payload_string),
                timeout=1
            )
            order_result = response.json()
            if 'code' not in order_result or order_result['code'] != 0:
                log.warning(f"訂單回應異常: {order_result}")
            return order_result
        except requests.exceptions.Timeout:
            log.warning(f"下單超時: {order_side} @ {order_price}")
        except Exception as err:
            log.error(f"下單失敗: {err}")
        return {}

    def cancel_single_order(self, order_identifier):
        """取消單個訂單"""
        api_endpoint = "/api/cancel_order"
        cancel_data = {"order_id": order_identifier}
        payload_string = json.dumps(cancel_data)
        
        try:
            self.http_session.post(
                self.api_url + api_endpoint,
                data=payload_string,
                headers=self._create_signature_headers(payload_string),
                timeout=1
            )
        except requests.exceptions.Timeout:
            log.warning(f"撤單超時: {order_identifier}")
        except Exception as err:
            log.error(f"撤單失敗: {err}")

    def execute_market_close(self, close_side, close_quantity):
        """執行市價平倉"""
        api_endpoint = "/api/new_order"
        quantity_str = str(abs(float(close_quantity)))
        close_order = {
            "symbol": TRADING_PAIR,
            "side": close_side,
            "order_type": "market",
            "qty": quantity_str,
            "time_in_force": "ioc",
            "reduce_only": True
        }
        payload_string = json.dumps(close_order)
        
        try:
            print(f"🔥 糟糕了有單，發送市價平倉單: {close_side} {quantity_str}")
            log.info(f"執行平倉: {close_side} {quantity_str}")
            response = self.http_session.post(
                self.api_url + api_endpoint,
                data=payload_string,
                headers=self._create_signature_headers(payload_string),
                timeout=2
            )
            close_result = response.json()
            print(f"   => 結果: {close_result}")
            log.info(f"平倉回應: {close_result}")
        except requests.exceptions.Timeout:
            log.error("平倉請求超時")
            print("   => 平倉請求超時")
        except Exception as err:
            log.error(f"平倉執行失敗: {err}")
            print(f"   => 平倉請求失敗: {err}")

# ==========================================
# 🛡️ 系統退出管理
# ==========================================

def perform_emergency_shutdown(bot_instance):
    """緊急關閉：撤單並平倉"""
    try:
        print("\n" + "="*50)
        print("🚨 執行緊急關閉程序...")
        log.warning("開始緊急關閉流程")
        
        # 檢查持倉狀態
        current_position = bot_instance.query_current_position()
        position_exists = False
        position_quantity = 0.0
        
        if current_position:
            position_quantity = float(current_position.get('qty', 0))
            if position_quantity != 0:
                position_exists = True
        
        # 批量撤單
        active_orders = bot_instance.query_active_orders()
        if active_orders:
            print(f"📋 撤銷 {len(active_orders)} 個掛單...")
            log.info(f"開始撤銷 {len(active_orders)} 個訂單")
            
            cancel_workers = []
            for order_item in active_orders:
                worker = threading.Thread(
                    target=bot_instance.cancel_single_order,
                    args=(order_item['id'],)
                )
                worker.start()
                cancel_workers.append(worker)
            
            for worker in cancel_workers:
                worker.join(timeout=2)
            
            print("✅ 所有訂單已撤銷")
            time.sleep(1)
        
        # 處理持倉
        if position_exists:
            print(f"💼 檢測到持倉: {position_quantity}")
            log.warning(f"執行緊急平倉: {position_quantity}")
            
            closing_side = 'sell' if position_quantity > 0 else 'buy'
            bot_instance.execute_market_close(closing_side, abs(position_quantity))
            time.sleep(1)
            
            # 驗證平倉結果
            verification_position = bot_instance.query_current_position()
            if verification_position and float(verification_position.get('qty', 0)) != 0:
                print("⚠️ 平倉可能未完成，請手動確認")
                log.error("平倉驗證失敗")
            else:
                print("✅ 持倉已平倉")
        else:
            print("✅ 無持倉，安全退出")
        
        print("="*50)
        log.info("緊急關閉流程完成")
        
    except Exception as err:
        print(f"❌ 緊急關閉時發生錯誤: {err}")
        log.error(f"緊急關閉錯誤: {err}")

def handle_shutdown_signal(signal_number, stack_frame):
    """處理系統中斷信號"""
    global is_shutting_down, trading_bot
    
    print("\n\n🛑 收到中斷信號 (Ctrl+C)...")
    log.warning("收到 SIGINT 信號")
    
    is_shutting_down = True
    
    if trading_bot:
        perform_emergency_shutdown(trading_bot)
    
    print("👋 程式已安全退出")
    sys.exit(0)

# ==========================================
# 🎯 主策略執行邏輯
# ==========================================

def execute_trading_strategy():
    global trading_bot, is_shutting_down
    
    # 驗證配置
    if not API_KEY:
        print("❌ 請在 .env 文件中設置 API_KEY！")
        log.error("API_KEY 未配置")
        return

    # 處理私鑰
    if not SIGNING_KEY:
        print("❌ 請在 .env 文件中設置 SIGNING_KEY！")
        log.error("私鑰配置缺失")
        return
    
    print("🔑 偵測到簽名密鑰，正在轉換...")
    log.info("開始轉換 Base58 私鑰")
    final_private_key = decode_base58_private_key(SIGNING_KEY)
    if not final_private_key:
        print("❌ 簽名密鑰轉換失敗，請檢查格式！")
        log.error("Base58 私鑰轉換失敗")
        return
    print("✅ 簽名密鑰轉換成功！")
    log.info("Base58 私鑰轉換完成")

    # 註冊信號處理
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    
    # 初始化機器人
    trading_bot = TradingBot(API_KEY, final_private_key)
    
    # 初始化成交記錄器
    trade_logger = TradeLogger("trades.log")
    
    print("💡 提示: 按 Ctrl+C 可安全退出（會自動撤單和平倉）")
    print("📝 成交記錄將保存到: trades.log")
    time.sleep(2)
    
    # 冷靜期管理
    volatility_resume_at = datetime.min
    position_resume_at = datetime.min
    
    # 價格歷史記錄
    historical_prices = deque(maxlen=PRICE_HISTORY_SIZE)
    log.info(f"價格歷史緩衝區: {historical_prices.maxlen} 筆")

    # 主循環
    while True:
        try:
            if is_shutting_down:
                log.info("偵測到關閉信號，退出主循環")
                break
            
            action_messages = []
            
            # 檢查吃單後冷靜期
            if datetime.now() < position_resume_at:
                time_remaining = int((position_resume_at - datetime.now()).total_seconds())
                
                os.system('cls' if os.name == 'nt' else 'clear')
                print("=== 🧊 吃單後冷靜期 🧊 ===")
                print(f"⏰ 剩餘時間: {time_remaining // 60}分 {time_remaining % 60}秒")
                print("🛡️ 暫停掛單中，等待市場穩定...")
                print("💡 此期間不會進行任何交易")
                time.sleep(1)
                continue
            
            # 查詢持倉狀態
            current_position = trading_bot.query_current_position()
            has_open_position = False
            position_size = 0.0
            
            if current_position:
                position_size = float(current_position.get('qty', 0))
                if position_size != 0:
                    has_open_position = True

            # 處理持倉平倉
            if has_open_position:
                print(f"🚨🚨🚨 完蛋啦！吃到單 qty={position_size}，平倉閃人中！ 🚨🚨🚨")
                log.warning(f"檢測到持倉: {position_size}")
                
                # 獲取當前市場價格用於記錄
                current_market_price = trading_bot.market_stream.fetch_current_price()
                if current_market_price is None:
                    current_market_price = trading_bot.fetch_backup_price()
                
                # 獲取詳細訂單簿深度（每個價格檔位）
                detailed_orderbook = None
                if current_market_price:
                    detailed_orderbook = trading_bot.market_stream.get_detailed_orderbook_depth(current_market_price)
                
                # 計算波動率（使用歷史價格數據）
                short_term_vol = None
                mid_term_vol = None
                if current_market_price and historical_prices:
                    current_ts = time.time()
                    # 20秒波動
                    if historical_prices:
                        oldest_price = historical_prices[0][1]
                        mid_term_vol = abs(current_market_price - oldest_price) / oldest_price
                    
                    # 10秒波動
                    cutoff_time = current_ts - 10
                    base_price = current_market_price
                    for timestamp, price in historical_prices:
                        if timestamp >= cutoff_time:
                            base_price = price
                            break
                    short_term_vol = abs(current_market_price - base_price) / base_price
                
                # 記錄成交信息（包含詳細訂單簿深度和波動率）
                trade_logger.log_trade(
                    current_position, 
                    market_price=current_market_price,
                    detailed_orderbook=detailed_orderbook,
                    short_term_volatility=short_term_vol,
                    mid_term_volatility=mid_term_vol
                )
                
                # 並行撤單
                active_orders = trading_bot.query_active_orders()
                log.info(f"撤銷 {len(active_orders)} 個訂單")
                
                cancel_workers = []
                for order_item in active_orders:
                    worker = threading.Thread(
                        target=trading_bot.cancel_single_order,
                        args=(order_item['id'],)
                    )
                    worker.start()
                    cancel_workers.append(worker)
                
                for worker in cancel_workers:
                    worker.join(timeout=2)
                
                log.info("訂單已全部撤銷")
                time.sleep(1.5)
                
                # 確定平倉方向
                closing_direction = 'sell' if position_size > 0 else 'buy'
                log.info(f"平倉方向: {closing_direction}, 數量: {abs(position_size)}")
                
                # 重試平倉邏輯
                for retry_attempt in range(3):
                    try:
                        trading_bot.execute_market_close(closing_direction, abs(position_size))
                        time.sleep(1)
                        
                        # 驗證平倉
                        verify_position = trading_bot.query_current_position()
                        if verify_position and float(verify_position.get('qty', 0)) != 0:
                            log.warning(f"平倉嘗試 {retry_attempt+1}/3 未完成")
                            if retry_attempt < 2:
                                print(f"⚠️ 平倉未完成，重試中... ({retry_attempt+1}/3)")
                                time.sleep(1)
                                continue
                            else:
                                print("❌ 平倉失敗，請手動處理！")
                                log.error("平倉失敗，已達最大重試次數")
                        else:
                            print("✅ 平倉成功！")
                            log.info("平倉完成")
                            
                            # 獲取平倉時的市場價格
                            close_market_price = trading_bot.market_stream.fetch_current_price()
                            if close_market_price is None:
                                close_market_price = trading_bot.fetch_backup_price()
                            
                            # 記錄平倉信息
                            close_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            close_price = close_market_price if close_market_price else 'N/A'
                            closing_info = {
                                'close_time': close_time,
                                'close_price': close_price
                            }
                            # 更新日誌，添加平倉信息
                            trade_logger.log_trade(current_position, market_price=close_market_price, closing_info=closing_info)
                            
                            break
                    except Exception as err:
                        log.error(f"平倉重試 {retry_attempt+1} 錯誤: {err}")
                        if retry_attempt < 2:
                            time.sleep(1)
                
                # 設定冷靜期
                position_resume_at = datetime.now() + timedelta(seconds=POSITION_PAUSE_DURATION)
                log.warning(f"進入吃單後冷靜期 {POSITION_PAUSE_DURATION//60} 分鐘")
                print(f"🧊 進入 {POSITION_PAUSE_DURATION//60} 分鐘冷靜期，暫停掛單...")
                
                time.sleep(2)
                continue

            # 獲取當前價格
            reference_price = trading_bot.market_stream.fetch_current_price()
            price_source_label = "WS-Price"
            
            if reference_price is None:
                reference_price = trading_bot.fetch_backup_price()
                price_source_label = "HTTP"
            
            if reference_price is None or reference_price == 0:
                print("❌ 無法獲取價格...")
                time.sleep(1)
                continue

            # 波動保護檢查
            if datetime.now() < volatility_resume_at:
                time_remaining = int((volatility_resume_at - datetime.now()).total_seconds())
                historical_prices.clear()
                
                os.system('cls' if os.name == 'nt' else 'clear')
                print("=== ❄️ 市場趨勢過大，進入冷靜期 ❄️ ===")
                print(f"⏰ 剩餘時間: {time_remaining // 60}分 {time_remaining % 60}秒")
                print(f"📊 目前價格: {int(reference_price):,}")
                print("🛡️ 暫停掛單中，等待行情穩定...")
                time.sleep(1)
                continue

            # 記錄價格歷史
            current_timestamp = time.time()
            historical_prices.append((current_timestamp, reference_price))

            # 清理舊數據
            while historical_prices and historical_prices[0][0] < current_timestamp - 20:
                historical_prices.popleft()

            # 計算波動率
            short_term_volatility = 0.0
            mid_term_volatility = 0.0
            
            if historical_prices:
                # 20秒波動
                oldest_price = historical_prices[0][1]
                mid_term_volatility = abs(reference_price - oldest_price) / oldest_price
                
                # 10秒波動
                cutoff_time = current_timestamp - 10
                base_price = reference_price
                for timestamp, price in historical_prices:
                    if timestamp >= cutoff_time:
                        base_price = price
                        break
                short_term_volatility = abs(reference_price - base_price) / base_price

            # 計算價差
            current_spread = 0.0
            if (trading_bot.market_stream.price_data_ready and 
                trading_bot.market_stream.current_ask > trading_bot.market_stream.current_bid):
                current_spread = (
                    (trading_bot.market_stream.current_ask - trading_bot.market_stream.current_bid) / 
                    reference_price * 10000
                )

            # 計算 OBI
            orderbook_imbalance = trading_bot.market_stream.compute_orderbook_imbalance(reference_price)
            imbalance_magnitude = abs(orderbook_imbalance) if orderbook_imbalance is not None else 0.0

            # 風控觸發檢查
            market_is_dangerous = False
            danger_reason = ""
            pause_duration = MARKET_PAUSE_DURATION

            if orderbook_imbalance is not None and imbalance_magnitude > ORDERBOOK_IMBALANCE_LIMIT:
                market_is_dangerous = True
                danger_reason = f"OBI不平衡 ({orderbook_imbalance*100:.1f}%, 閾值{ORDERBOOK_IMBALANCE_LIMIT*100:.0f}%)"
                pause_duration = ORDERBOOK_PAUSE_DURATION
            elif current_spread > SPREAD_DANGER_THRESHOLD:
                market_is_dangerous = True
                danger_reason = f"Spread價差過大 ({current_spread:.1f}bps)"
            elif short_term_volatility > VOLATILITY_SHORT_TERM_PCT:
                market_is_dangerous = True
                danger_reason = f"10秒趨勢劇烈 ({short_term_volatility*100:.2f}%)"
            elif mid_term_volatility > VOLATILITY_MID_TERM_PCT:
                market_is_dangerous = True
                danger_reason = f"20秒趨勢劇烈 ({mid_term_volatility*100:.2f}%)"

            if market_is_dangerous:
                print(f"🌊 偵測到危險行情! 原因: {danger_reason}")
                if orderbook_imbalance is not None and imbalance_magnitude > ORDERBOOK_IMBALANCE_LIMIT:
                    print(f"🛡️ 撤銷所有訂單並暫停交易 {pause_duration} 秒...")
                else:
                    print(f"🛡️ 撤銷所有訂單並暫停交易 {pause_duration//60} 分鐘...")
                log.warning(f"觸發風控保護: {danger_reason}")
                
                # 並行撤單
                active_orders = trading_bot.query_active_orders()
                log.info(f"開始撤銷 {len(active_orders)} 個訂單")
                
                cancel_workers = []
                for order_item in active_orders:
                    worker = threading.Thread(
                        target=trading_bot.cancel_single_order,
                        args=(order_item['id'],)
                    )
                    worker.start()
                    cancel_workers.append(worker)
                
                for worker in cancel_workers:
                    worker.join(timeout=2)
                
                log.info("訂單已撤銷，進入冷靜期")
                volatility_resume_at = datetime.now() + timedelta(seconds=pause_duration)
                time.sleep(1)
                continue
            
            # 計算目標價格
            spread_factor = SPREAD_TARGET_BPS / 10000
            target_buy_price = math.floor(reference_price * (1 - spread_factor))
            target_sell_price = math.ceil(reference_price * (1 + spread_factor))

            # 訂單管理
            active_orders = trading_bot.query_active_orders()
            buy_order_exists = False
            sell_order_exists = False

            for order_info in active_orders:
                order_id = order_info['id']
                order_price = float(order_info['price'])
                order_direction = order_info['side']
                deviation_bps = abs(reference_price - order_price) / reference_price * 10000
                
                if deviation_bps < SPREAD_MIN_BPS or deviation_bps > SPREAD_MAX_BPS:
                    trading_bot.cancel_single_order(order_id)
                    action_messages.append(f"⚠️ {order_direction} 偏離 {deviation_bps:.1f}bps -> 撤單")
                else:
                    if order_direction == 'buy':
                        buy_order_exists = True
                    if order_direction == 'sell':
                        sell_order_exists = True

            # 補充買單
            if not buy_order_exists:
                order_response = trading_bot.submit_limit_order('buy', target_buy_price)
                if 'code' in order_response and order_response['code'] == 0:
                    action_messages.append(f"✅ 掛買單 @ {int(target_buy_price)}")
            
            # 補充賣單
            if not sell_order_exists:
                order_response = trading_bot.submit_limit_order('sell', target_sell_price)
                if 'code' in order_response and order_response['code'] == 0:
                    action_messages.append(f"✅ 掛賣單 @ {int(target_sell_price)}")

            # 顯示界面
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"⏰ 台灣時間： {datetime.now().strftime('%H:%M:%S')}")
            print(f"📊 即時價格: {int(reference_price):,} ({price_source_label}) [Spread: {current_spread:.1f}bps]")
            print(f"📈 10秒波動: {short_term_volatility*100:.3f}% (限{VOLATILITY_SHORT_TERM_PCT*100}%)")
            print(f"📈 20秒波動: {mid_term_volatility*100:.3f}% (限{VOLATILITY_MID_TERM_PCT*100}%)")
            
            # OBI 顯示
            if orderbook_imbalance is not None:
                status_indicator = "🟢" if imbalance_magnitude <= ORDERBOOK_IMBALANCE_LIMIT else "🔴"
                if abs(orderbook_imbalance) < 0.01:
                    balance_label = "平衡"
                elif orderbook_imbalance > 0:
                    balance_label = "買盤多"
                else:
                    balance_label = "賣盤多"
                print(f"📊 OBI指標: {status_indicator} {orderbook_imbalance*100:.1f}% ({balance_label}, 閾值{ORDERBOOK_IMBALANCE_LIMIT*100:.0f}%)")
            else:
                print("📊 OBI指標: ⚠️ 數據未就緒")
            
            if trading_bot.market_stream.price_data_ready:
                print(f"🟢 買方單: {int(trading_bot.market_stream.current_bid):,} 🔴 賣方單: {int(trading_bot.market_stream.current_ask):,}")
            
            print("🛡️ 現在沒有持倉")
            print("-" * 40)
            
            if not active_orders:
                print(" (無掛單，正在補單...)")
            
            for order_info in active_orders:
                price_deviation = abs(reference_price - float(order_info['price'])) / reference_price * 10000
                print(f" [{order_info['side'].upper()}] {int(float(order_info['price']))} (距 {price_deviation:.1f}bps)")
            
            print("-" * 40)
            for message in action_messages:
                print(message)

        except Exception as err:
            print(f"Error: {err}")
        
        time.sleep(LOOP_INTERVAL)

if __name__ == "__main__":
    execute_trading_strategy()
