"""
Telegram Bot - Monitor Pendle Market Creation on Robinhood Chain
Listens to: 0x98fa74aeb7c9e941500fbbe9e7950c179b76a52b
"""

import asyncio
import os
import json
import sqlite3
from dotenv import load_dotenv
from telegram import Bot
from web3 import Web3
from datetime import datetime
from typing import Dict, Optional

load_dotenv()

# Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ROBINHOOD_RPC = os.getenv("ROBINHOOD_RPC")

# Pendle Deployer on Robinhood Chain
PENDLE_DEPLOYER = "0x98fa74aeb7c9e941500fbbe9e7950c179b76a52b"

# Pendle Market Factory ABI - Events to monitor
PENDLE_ABI = json.loads('''[
    {
        "anonymous": false,
        "inputs": [
            {"indexed": true, "name": "market", "type": "address"},
            {"indexed": true, "name": "pt", "type": "address"},
            {"indexed": true, "name": "yt", "type": "address"},
            {"indexed": false, "name": "expiry", "type": "uint256"}
        ],
        "name": "MarketCreated",
        "type": "event"
    }
]''')

# ERC20 ABI for getting token info
ERC20_ABI = json.loads('''[
    {
        "constant": true,
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": true,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": true,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    }
]''')

class EventDatabase:
    """Track processed events to avoid duplicates"""
    def __init__(self, db_name="pendle_events.db"):
        self.db_name = db_name
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS markets (
                tx_hash TEXT PRIMARY KEY,
                market_address TEXT,
                pt_address TEXT,
                yt_address TEXT,
                expiry INTEGER,
                timestamp DATETIME,
                data JSON
            )
        ''')
        conn.commit()
        conn.close()
    
    def add_market(self, tx_hash: str, market: str, pt: str, yt: str, expiry: int, data: dict):
        conn = sqlite3.connect(self.db_name)
        c = conn.cursor()
        c.execute('''
            INSERT OR IGNORE INTO markets 
            (tx_hash, market_address, pt_address, yt_address, expiry, timestamp, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (tx_hash, market, pt, yt, expiry, datetime.now(), json.dumps(data)))
        conn.commit()
        conn.close()
    
    def market_exists(self, tx_hash: str) -> bool:
        conn = sqlite3.connect(self.db_name)
        c = conn.cursor()
        c.execute('SELECT 1 FROM markets WHERE tx_hash = ?', (tx_hash,))
        exists = c.fetchone() is not None
        conn.close()
        return exists

class TokenInfo:
    """Get token name and symbol"""
    def __init__(self, web3: Web3):
        self.web3 = web3
        self.cache = {}
    
    def get_token_symbol(self, token_address: str) -> str:
        """Get token symbol, cached"""
        if token_address in self.cache:
            return self.cache[token_address]
        
        try:
            # Add checksum to address
            token_address = self.web3.to_checksum_address(token_address)
            token = self.web3.eth.contract(address=token_address, abi=ERC20_ABI)
            
            symbol = token.functions.symbol().call()
            self.cache[token_address] = symbol
            return symbol
        except Exception as e:
            print(f"Error getting symbol for {token_address}: {e}")
            return "UNKNOWN"
    
    def get_token_name(self, token_address: str) -> str:
        """Get token name"""
        try:
            token_address = self.web3.to_checksum_address(token_address)
            token = self.web3.eth.contract(address=token_address, abi=ERC20_ABI)
            name = token.functions.name().call()
            return name
        except:
            return "Unknown"

class PendleMonitor:
    """Monitor Pendle market creation on Robinhood Chain"""
    
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_TOKEN)
        self.web3 = Web3(Web3.HTTPProvider(ROBINHOOD_RPC))
        self.db = EventDatabase()
        self.token_info = TokenInfo(self.web3)
        self.last_block = None
        
        # Verify connection
        if not self.web3.is_connected():
            print("❌ Cannot connect to RPC")
            raise Exception("RPC connection failed")
        
        print(f"✅ Connected to {ROBINHOOD_RPC}")
        print(f"📍 Monitoring Pendle deployer: {PENDLE_DEPLOYER}")
    
    async def send_message(self, text: str):
        """Send Telegram message"""
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            print(f"✅ Message sent to Telegram")
        except Exception as e:
            print(f"❌ Error sending message: {e}")
    
    def decode_market_event(self, event) -> Dict:
        """Decode Pendle MarketCreated event"""
        try:
            market = event['args']['market']
            pt = event['args']['pt']
            yt = event['args']['yt']
            expiry = event['args']['expiry']
            
            # Get token symbols
            pt_symbol = self.token_info.get_token_symbol(pt)
            yt_symbol = self.token_info.get_token_symbol(yt)
            
            # Convert expiry to readable date
            expiry_date = datetime.fromtimestamp(expiry)
            
            return {
                'market': market,
                'pt': pt,
                'yt': yt,
                'pt_symbol': pt_symbol,
                'yt_symbol': yt_symbol,
                'expiry': expiry,
                'expiry_date': expiry_date,
                'tx_hash': event['transactionHash'].hex(),
                'block': event['blockNumber']
            }
        except Exception as e:
            print(f"❌ Error decoding event: {e}")
            return None
    
    def format_market_message(self, decoded: Dict) -> str:
        """Format decoded market data for Telegram"""
        expiry_str = decoded['expiry_date'].strftime('%Y-%m-%d %H:%M:%S UTC')
        days_until = (decoded['expiry_date'] - datetime.now()).days
        
        message = f"""
🎯 <b>NEW PENDLE MARKET CREATED</b>

<b>💰 Principal Token (PT):</b>
<code>{decoded['pt']}</code>
Symbol: <b>{decoded['pt_symbol']}</b>

<b>⏰ Yield Token (YT):</b>
<code>{decoded['yt']}</code>
Symbol: <b>{decoded['yt_symbol']}</b>

<b>📍 Market Address:</b>
<code>{decoded['market']}</code>

<b>📅 Expiry Date:</b>
{expiry_str}
({days_until} days from now)

<b>📊 Details:</b>
Block: {decoded['block']}
TX: <a href="https://roninblock.com/tx/{decoded['tx_hash']}">View on Explorer</a>
"""
        return message
    
    async def check_for_new_markets(self):
        """Check for new Pendle market creation events"""
        try:
            if self.last_block is None:
                self.last_block = self.web3.eth.block_number - 50
                print(f"Starting from block {self.last_block}")
                return
            
            current_block = self.web3.eth.block_number
            
            if current_block <= self.last_block:
                return  # No new blocks
            
            print(f"[{datetime.now()}] Checking blocks {self.last_block} to {current_block}")
            
            # Create contract instance
            contract = self.web3.eth.contract(
                address=Web3.to_checksum_address(PENDLE_DEPLOYER),
                abi=PENDLE_ABI
            )
            
            # Get MarketCreated events
            events = contract.events.MarketCreated.get_logs(
                from_block=self.last_block,
                to_block=current_block
            )
            
            print(f"Found {len(events)} events")
            
            for event in events:
                tx_hash = event['transactionHash'].hex()
                
                # Skip if already processed
                if self.db.market_exists(tx_hash):
                    print(f"Event already processed: {tx_hash}")
                    continue
                
                # Decode event
                decoded = self.decode_market_event(event)
                if not decoded:
                    continue
                
                # Save to database
                self.db.add_market(
                    tx_hash,
                    decoded['market'],
                    decoded['pt'],
                    decoded['yt'],
                    decoded['expiry'],
                    decoded
                )
                
                # Format and send message
                message = self.format_market_message(decoded)
                await self.send_message(message)
                
                print(f"✅ Processed new market: {decoded['market']}")
            
            self.last_block = current_block
            
        except Exception as e:
            print(f"❌ Error checking markets: {e}")
            import traceback
            traceback.print_exc()
    
    async def start_monitoring(self):
        """Start the monitoring loop"""
        print("\n🤖 Starting Pendle Market Monitor...")
        print(f"🔗 RPC: {ROBINHOOD_RPC}")
        print(f"📍 Contract: {PENDLE_DEPLOYER}")
        print(f"💬 Chat ID: {TELEGRAM_CHAT_ID}\n")
        
        try:
            await self.send_message("✅ Pendle Market Monitor started! Listening for new market creations...")
        except Exception as e:
            print(f"Warning: Could not send startup message: {e}")
        
        while True:
            try:
                await self.check_for_new_markets()
                await asyncio.sleep(12)  # Check every 12 seconds
            except KeyboardInterrupt:
                print("\n👋 Bot stopped")
                break
            except Exception as e:
                print(f"❌ Error in monitoring loop: {e}")
                await asyncio.sleep(30)

async def main():
    monitor = PendleMonitor()
    await monitor.start_monitoring()

if __name__ == "__main__":
    asyncio.run(main())


