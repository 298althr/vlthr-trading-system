"""
Telegram Polling Service for VLTHR 3-Bot Architecture
Receives incoming messages from Telegram and processes commands
"""

import os
import requests
import time
import threading
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from telegram_service import TelegramService, BotType


@dataclass
class TelegramUpdate:
    update_id: int
    message_id: int
    chat_id: str
    text: str
    from_user: str
    bot_token: str


class TelegramPollingService:
    """Polling service to receive and process Telegram messages"""
    
    def __init__(self):
        self.service = TelegramService()
        self.running = False
        self.update_offsets = {
            "data": 0,
            "trading": 0,
            "devops": 0
        }
        self.poll_interval = 2  # seconds
    
    def get_updates(self, bot_token: str, offset: int = 0) -> Tuple[List[TelegramUpdate], int]:
        """Get updates from Telegram Bot API. Returns (updates, next_offset)."""
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
            params = {
                "offset": offset,
                "timeout": 10,
                "allowed_updates": ["message"]
            }
            
            response = requests.get(url, params=params, timeout=15)
            next_offset = offset
            
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    raw_updates = data.get("result", [])
                    updates = []
                    for update in raw_updates:
                        message = update.get("message", {})
                        # Get text or caption (for photos/videos with caption)
                        text = message.get("text", "") or message.get("caption", "")
                        update_id = update.get("update_id", 0)
                        
                        # Always advance offset past this update
                        next_offset = update_id + 1
                        
                        # Only process messages with text/caption
                        if text and text.strip():
                            updates.append(TelegramUpdate(
                                update_id=update_id,
                                message_id=message.get("message_id"),
                                chat_id=str(message.get("chat", {}).get("id")),
                                text=text.strip(),
                                from_user=message.get("from", {}).get("username", "unknown"),
                                bot_token=bot_token
                            ))
                    
                    return updates, next_offset
            return [], next_offset
        except Exception as e:
            print(f"Error getting updates: {e}", flush=True)
            return [], offset
    
    def parse_command(self, text: str) -> tuple:
        """Parse command and arguments from message text"""
        text = text.strip()
        
        # Check for bot mention (e.g., @vlthr_data_bot /start)
        if "@" in text:
            parts = text.split()
            bot_mention = parts[0]
            command = parts[1] if len(parts) > 1 else ""
            args = parts[2:] if len(parts) > 2 else []
            return bot_mention, command, args
        else:
            # Direct command (e.g., /start)
            parts = text.split()
            command = parts[0]
            args = parts[1:] if len(parts) > 1 else []
            return None, command, args
    
    def determine_bot(self, bot_mention: str, bot_token: str) -> str:
        """Determine which bot the message is for"""
        if bot_mention:
            mention_lower = bot_mention.lower()
            if "data" in mention_lower:
                return self.service.data_bot_username
            elif "trader" in mention_lower or "trading" in mention_lower:
                return self.service.trading_bot_username
            elif "devops" in mention_lower:
                return self.service.devops_bot_username
        
        # If no bot mention, determine by token
        if bot_token == self.service.data_bot_token:
            return self.service.data_bot_username
        elif bot_token == self.service.trading_bot_token:
            return self.service.trading_bot_username
        elif bot_token == self.service.devops_bot_token:
            return self.service.devops_bot_username
        
        return None
    
    def process_update(self, update: TelegramUpdate):
        """Process a single Telegram update"""
        try:
            bot_mention, command, args = self.parse_command(update.text)
            bot_username = self.determine_bot(bot_mention, update.bot_token)
            
            print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] "
                  f"User: {update.from_user} | "
                  f"Chat: {update.chat_id} | "
                  f"Bot: {bot_username} | "
                  f"Command: {command} | "
                  f"Args: {args}", flush=True)
            
            # Process command
            if bot_username and command:
                # If no mention and command is not in this bot's registry, ignore to avoid spam
                if bot_mention is None and command not in self.service.commands.get(bot_username, {}):
                    print(f"Ignoring {command} for {bot_username} (no mention, not in registry)", flush=True)
                    return
                
                response = self.service.process_command(bot_username, command, args)
                print(f"Generated response: {response[:80]}...", flush=True)
                
                # Send response via the appropriate bot to the same chat the command came from
                if bot_username == self.service.data_bot_username:
                    success, msg = self.service.send_alert(BotType.DATA, response, chat_id=update.chat_id)
                elif bot_username == self.service.trading_bot_username:
                    success, msg = self.service.send_alert(BotType.TRADING, response, chat_id=update.chat_id)
                elif bot_username == self.service.devops_bot_username:
                    success, msg = self.service.send_alert(BotType.DEVOPS, response, chat_id=update.chat_id)
                print(f"Send result: {success} - {msg}", flush=True)
            else:
                print(f"Could not determine bot or command for message: {update.text}", flush=True)
            
        except Exception as e:
            print(f"Error processing update: {e}", flush=True)
            import traceback
            traceback.print_exc()
    
    def poll_bot(self, bot_type: str, bot_token: str):
        """Poll a single bot for updates"""
        offset = self.update_offsets[bot_type]
        
        while self.running:
            try:
                updates, next_offset = self.get_updates(bot_token, offset)
                
                # Always advance offset to skip non-text service messages
                if next_offset > offset:
                    offset = next_offset
                    self.update_offsets[bot_type] = offset
                
                if updates:
                    for update in updates:
                        self.process_update(update)
                
                time.sleep(self.poll_interval)
                
            except Exception as e:
                print(f"Error polling {bot_type} bot: {e}", flush=True)
                time.sleep(5)
    
    def start(self):
        """Start polling all bots"""
        self.running = True
        print("Starting Telegram polling service...")
        print(f"Polling interval: {self.poll_interval}s")
        print(f"Data bot: {self.service.data_bot_username}")
        print(f"Trading bot: {self.service.trading_bot_username}")
        print(f"Devops bot: {self.service.devops_bot_username}")
        print("-" * 50)
        
        # Start polling threads for each bot
        threads = []
        
        # Data bot thread
        data_thread = threading.Thread(
            target=self.poll_bot,
            args=("data", self.service.data_bot_token),
            daemon=True
        )
        data_thread.start()
        threads.append(data_thread)
        
        # Trading bot thread
        trading_thread = threading.Thread(
            target=self.poll_bot,
            args=("trading", self.service.trading_bot_token),
            daemon=True
        )
        trading_thread.start()
        threads.append(trading_thread)
        
        # Devops bot thread
        devops_thread = threading.Thread(
            target=self.poll_bot,
            args=("devops", self.service.devops_bot_token),
            daemon=True
        )
        devops_thread.start()
        threads.append(devops_thread)
        
        # Keep main thread alive
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping polling service...")
            self.stop()
    
    def stop(self):
        """Stop polling"""
        self.running = False
        print("Polling service stopped.")


if __name__ == "__main__":
    # Tokens must be set via environment variables (.env or docker env_file)
    required = ["DATA_TELEGRAM_BOT_TOKEN", "TRADER_TELEGRAM_BOT_TOKEN",
                "DEVOPS_TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your Telegram bot tokens.")
        sys.exit(1)

    service = TelegramPollingService()
    service.start()
