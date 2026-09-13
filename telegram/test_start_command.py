"""
Test script for /start command
Sends /start command via all 3 bots to verify help message
"""

import os
from telegram_service import TelegramService, BotType

# Load environment variables from .env (do not hardcode tokens)
required = ["DATA_TELEGRAM_BOT_TOKEN", "TRADER_TELEGRAM_BOT_TOKEN",
            "DEVOPS_TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    print(f"ERROR: Missing environment variables: {', '.join(missing)}")
    print("Copy .env.example to .env and fill in your Telegram bot tokens.")
    sys.exit(1)

def test_start_command():
    """Test /start command via all bots"""
    service = TelegramService()
    
    print("=== Testing /start Command ===\n")
    
    # Get the start message
    start_message = service.cmd_start([])
    print("Start message preview:")
    print(start_message[:500] + "...\n")
    
    # Send via data bot
    print("1. Sending /start via DATA bot...")
    success, msg = service.send_alert(BotType.DATA, start_message)
    print(f"   Result: {success}")
    
    # Send via trading bot
    print("\n2. Sending /start via TRADING bot...")
    success, msg = service.send_alert(BotType.TRADING, start_message)
    print(f"   Result: {success}")
    
    # Send via devops bot
    print("\n3. Sending /start via DEVOPS bot...")
    success, msg = service.send_alert(BotType.DEVOPS, start_message)
    print(f"   Result: {success}")
    
    print("\n=== /start Command Test Complete ===")
    print("Check your Telegram chat for the help message from all 3 bots.")

if __name__ == "__main__":
    test_start_command()
