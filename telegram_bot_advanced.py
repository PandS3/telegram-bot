import asyncio
import os
import json
import sqlite3
from dotenv import load_dotenv
from telegram import Bot
from web3 import Web3
from datetime import datetime
from typing import Dict, List

load_dotenv()

# Configuration
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ROBINHOOD_RPC = os.getenv("ROBINHOOD_RPC", "https://rpc.roninchain.com")

class EventDatabase:
    """SQLite database to track processed events"""
    def __init__(self, db_name="events.db"):
        self.db_name = db_name
        self.init_db()
    
    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS events (
                tx_hash TEXT PRIMARY KEY,
                event_type TEXT,
                contract TEXT,
                timestamp DATETIME,
                data JSON
            )
        ''')
        conn.commit()
        conn.close()
    
    def add_event(self, tx_hash: str, event_type: str, contract: str, data: dict):
        conn = sqlite3.connect(self.db_name)
        c = conn.cursor()
        c.execute('''
            INSERT OR IGNORE INTO events (tx_hash, event_type, contract, timestamp, data)
            VALUES (?, ?, ?, ?, ?)
        ''', (tx_hash, event_type, contract, datetime.now(), json.dumps(data)))
        conn.commit()
        conn.close()
    
    def event_exists(self, tx_hash: str) -> bool:
        conn = sqlite3.connect(self.db_name)
        c = conn.cursor()
        c.execute('SELECT 1 FROM events WHERE tx_hash = ?', (tx_hash,))
        exists = c.fetchone() is not None
        conn.close()
        return exists

class PendleMonitor:
    """Monitors Pendle protocol on Robinhood Chain"""
    
    def __init__(self, web3: Web3, db: EventDatabase, bot: Bot, chat_id: str):
        self.web3 = web3
        self.db = db
        self.bot = bot
        self.chat_id = chat_id
        self.last_block = None
        
        # Pendle Factory contract ABI (simplified)
        self.abi = json.loads('''[
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
    
    async def check_markets(self, factory_address: str):
        """Check for new Pendle markets"""
        try:
            if self.last_block is None:
                self.last_block = self.web3.eth.block_number - 100
            
            current_block = self.web3.eth.block_number
            
            if current_block <= self.last_block:
                return
            
            contract = self.web3.eth.contract(address=factory_address, abi=self.abi)
            events = contract.events.MarketCreated.get_logs(
                from_block=self.last_block,
                to_block=current_block
            )
            
            for event in events:
                tx_hash = event['transactionHash'].hex()
                
                if not self.db.event_exists(tx_hash):
                    self.db.add_event(tx_hash, 'pendle_market', factory_address, {
                        'market': event['args']['market'],
                        'pt': event['args']['pt'],
                        'yt': event['args']['yt'],
                        'expiry': event['args']['expiry']
                    })
                    
                    # Get transaction details
                    tx_receipt = self.web3.eth.get_transaction_receipt(tx_hash)
                    block = self.web3.eth.get_block(tx_receipt['blockNumber'])
                    
                    message = self._format_pendle_message(event, block)
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=message,
                        parse_mode="HTML"
                    )
            
            self.last_block = current_block
            
        except Exception as e:
            print(f"Pendle monitor error: {e}")
    
    def _format_pendle_message(self, event, block) -> str:
        market = event['args']['market']
        pt = event['args']['pt']
        yt = event['args']['yt']
        expiry = event['args']['expiry']
        tx_hash = event['transactionHash'].hex()
        
        expiry_date = datetime.fromtimestamp(expiry).strftime('%Y-%m-%d')
        
        return f"""
<b>🎯 New Pendle Market Created</b>

<b>Market:</b> <code>{market}</code>
<b>Principal Token (PT):</b> <code>{pt}</code>
<b>Yield Token (YT):</b> <code>{yt}</code>
<b>Expiry:</b> {expiry_date}

<b>Transaction:</b> <a href="https://roninblock.com/tx/{tx_hash}">View on Explorer</a>
"""

class NetnetMonitor:
    """Monitors Netnet protocol on Robinhood Chain"""
    
    def __init__(self, web3: Web3, db: EventDatabase, bot: Bot, chat_id: str):
        self.web3 = web3
        self.db = db
        self.bot = bot
        self.chat_id = chat_id
        self.last_block = None
        
        # Simplified Netnet ABI
        self.abi = json.loads('''[
            {
                "anonymous": false,
                "inputs": [
                    {"indexed": true, "name": "bondId", "type": "uint256"},
                    {"indexed": true, "name": "issuer", "type": "address"},
                    {"indexed": false, "name": "collateral", "type": "address"},
                    {"indexed": false, "name": "amount", "type": "uint256"},
                    {"indexed": false, "name": "maturity", "type": "uint256"}
                ],
                "name": "BondCreated",
                "type": "event"
            }
        ]''')
    
    async def check_bonds(self, contract_address: str, token_decimals: int = 18):
        """Check for new Netnet bonds"""
        try:
            if self.last_block is None:
                self.last_block = self.web3.eth.block_number - 100
            
            current_block = self.web3.eth.block_number
            
            if current_block <= self.last_block:
                return
            
            contract = self.web3.eth.contract(address=contract_address, abi=self.abi)
            events = contract.events.BondCreated.get_logs(
                from_block=self.last_block,
                to_block=current_block
            )
            
            for event in events:
                tx_hash = event['transactionHash'].hex()
                
                if not self.db.event_exists(tx_hash):
                    self.db.add_event(tx_hash, 'netnet_bond', contract_address, {
                        'bond_id': event['args']['bondId'],
                        'issuer': event['args']['issuer'],
                        'collateral': event['args']['collateral'],
                        'amount': event['args']['amount'],
                        'maturity': event['args']['maturity']
                    })
                    
                    block = self.web3.eth.get_block(event['blockNumber'])
                    message = self._format_bond_message(event, block, token_decimals)
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=message,
                        parse_mode="HTML"
                    )
            
            self.last_block = current_block
            
        except Exception as e:
            print(f"Netnet monitor error: {e}")
    
    def _format_bond_message(self, event, block, decimals) -> str:
        bond_id = event['args']['bondId']
        issuer = event['args']['issuer']
        collateral = event['args']['collateral']
        amount = event['args']['amount'] / (10 ** decimals)
        maturity = event['args']['maturity']
        tx_hash = event['transactionHash'].hex()
        
        maturity_date = datetime.fromtimestamp(maturity).strftime('%Y-%m-%d %H:%M')
        
        return f"""
<b>🏦 New Netnet Bond Created</b>

<b>Bond ID:</b> #{bond_id}
<b>Issuer:</b> <code>{issuer}</code>
<b>Collateral:</b> <code>{collateral}</code>
<b>Amount:</b> {amount:.4f}
<b>Maturity:</b> {maturity_date}

<b>Transaction:</b> <a href="https://roninblock.com/tx/{tx_hash}">View on Explorer</a>
"""

class OnChainBot:
    """Main bot orchestrator"""
    
    def __init__(self):
        self.bot = Bot(token=TELEGRAM_TOKEN)
        self.web3 = Web3(Web3.HTTPProvider(ROBINHOOD_RPC))
        self.db = EventDatabase()
        
        self.pendle_monitor = PendleMonitor(self.web3, self.db, self.bot, TELEGRAM_CHAT_ID)
        self.netnet_monitor = NetnetMonitor(self.web3, self.db, self.bot, TELEGRAM_CHAT_ID)
    
    async def start(self, pendle_factory: str, netnet_contract: str):
        """Start monitoring both protocols"""
        print("🤖 Starting on-chain monitoring bot...")
        
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="✅ Bot started! Monitoring Pendle markets and Netnet bonds..."
            )
        except Exception as e:
            print(f"Could not send startup message: {e}")
        
        while True:
            try:
                print(f"[{datetime.now()}] Checking for new events...")
                
                # Check both protocols concurrently
                await asyncio.gather(
                    self.pendle_monitor.check_markets(pendle_factory),
                    self.netnet_monitor.check_bonds(netnet_contract)
                )
                
                await asyncio.sleep(12)  # Check every 12 seconds
                
            except Exception as e:
                print(f"Error: {e}")
                await asyncio.sleep(30)

async def main():
    # Replace with actual contract addresses on Robinhood Chain
    PENDLE_FACTORY = "0x..."  # Get from Pendle docs
    NETNET_CONTRACT = "0x..."  # Get from Netnet docs
    
    bot = OnChainBot()
    await bot.start(PENDLE_FACTORY, NETNET_CONTRACT)

if __name__ == "__main__":
    asyncio.run(main())
