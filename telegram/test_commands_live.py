"""
Live test script for Telegram commands
Sends actual command responses to Telegram to validate full workflow
"""

import os
import sys
from telegram_service import TelegramService, BotType

# Load environment variables from .env (do not hardcode tokens)
required = ["DATA_TELEGRAM_BOT_TOKEN", "TRADER_TELEGRAM_BOT_TOKEN",
            "DEVOPS_TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    print(f"ERROR: Missing environment variables: {', '.join(missing)}")
    print("Copy .env.example to .env and fill in your Telegram bot tokens.")
    sys.exit(1)

def test_data_bot_commands():
    """Test all data bot commands and send to Telegram"""
    service = TelegramService()
    
    print("=== Testing DATA Bot Commands ===\n")
    
    # Test /freshness
    print("1. Testing /freshness...")
    response = service.cmd_data_freshness([])
    success, msg = service.send_alert(BotType.DATA, response)
    print(f"   Sent: {success}")
    
    # Test /ingestion
    print("\n2. Testing /ingestion...")
    response = service.cmd_data_ingestion([])
    success, msg = service.send_alert(BotType.DATA, response)
    print(f"   Sent: {success}")
    
    # Test /gaps
    print("\n3. Testing /gaps...")
    response = service.cmd_data_gaps([])
    success, msg = service.send_alert(BotType.DATA, response)
    print(f"   Sent: {success}")
    
    # Test /alerts
    print("\n4. Testing /alerts...")
    response = service.cmd_data_alerts([])
    success, msg = service.send_alert(BotType.DATA, response)
    print(f"   Sent: {success}")
    
    print("\n=== DATA Bot Commands Complete ===")

def test_trading_bot_commands():
    """Test all trading bot commands and send to Telegram"""
    service = TelegramService()
    
    print("\n=== Testing TRADING Bot Commands ===\n")
    
    # Test /trades
    print("1. Testing /trades...")
    response = service.cmd_trading_trades([])
    success, msg = service.send_alert(BotType.TRADING, response)
    print(f"   Sent: {success}")
    
    # Test /account
    print("\n2. Testing /account...")
    response = service.cmd_trading_account([])
    success, msg = service.send_alert(BotType.TRADING, response)
    print(f"   Sent: {success}")
    
    # Test /signals
    print("\n3. Testing /signals...")
    response = service.cmd_trading_signals([])
    success, msg = service.send_alert(BotType.TRADING, response)
    print(f"   Sent: {success}")
    
    # Test /performance
    print("\n4. Testing /performance...")
    response = service.cmd_trading_performance([])
    success, msg = service.send_alert(BotType.TRADING, response)
    print(f"   Sent: {success}")
    
    print("\n=== TRADING Bot Commands Complete ===")

def test_devops_bot_commands():
    """Test all devops bot commands and send to Telegram"""
    service = TelegramService()
    
    print("\n=== Testing DEVOPS Bot Commands ===\n")
    
    # Test /status
    print("1. Testing /status...")
    response = service.cmd_devops_status([])
    success, msg = service.send_alert(BotType.DEVOPS, response)
    print(f"   Sent: {success}")
    
    # Test /containers
    print("\n2. Testing /containers...")
    response = service.cmd_devops_containers([])
    success, msg = service.send_alert(BotType.DEVOPS, response)
    print(f"   Sent: {success}")
    
    # Test /errors
    print("\n3. Testing /errors...")
    response = service.cmd_devops_errors([])
    success, msg = service.send_alert(BotType.DEVOPS, response)
    print(f"   Sent: {success}")
    
    # Test /config
    print("\n4. Testing /config...")
    response = service.cmd_devops_config([])
    success, msg = service.send_alert(BotType.DEVOPS, response)
    print(f"   Sent: {success}")
    
    print("\n=== DEVOPS Bot Commands Complete ===")

def test_periodic_reports():
    """Test periodic reports and send to Telegram"""
    service = TelegramService()
    
    print("\n=== Testing Periodic Reports ===\n")
    
    # Test pipeline report
    print("1. Testing pipeline report...")
    pipeline_report = service.generate_pipeline_report()
    success, msg = service.send_alert(BotType.TRADING, pipeline_report)
    print(f"   Sent: {success}")
    
    # Test ingestion report
    print("\n2. Testing ingestion report...")
    ingestion_report = service.generate_ingestion_report()
    success, msg = service.send_alert(BotType.DATA, ingestion_report)
    print(f"   Sent: {success}")
    
    print("\n=== Periodic Reports Complete ===")

if __name__ == "__main__":
    print("Telegram Live Command Test Suite")
    print("=" * 50)
    print("This will send actual messages to your Telegram chat!")
    print("Press Ctrl+C to cancel if you don't want to proceed.\n")
    
    import time
    time.sleep(3)
    
    # Run tests
    test_data_bot_commands()
    test_trading_bot_commands()
    test_devops_bot_commands()
    test_periodic_reports()
    
    print("\n" + "=" * 50)
    print("All live tests completed!")
    print("Check your Telegram chat for messages from all 3 bots.")
