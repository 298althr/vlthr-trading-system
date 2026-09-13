"""
Test script for Telegram service
Sends test messages to verify bot tokens and functionality
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

def test_bot_tokens():
    """Test that all bot tokens can send messages"""
    service = TelegramService()
    
    print("=== Testing Bot Tokens ===\n")
    
    # Test data bot
    print("1. Testing DATA bot...")
    success, msg = service.send_alert(BotType.DATA, "🧪 Test message from @vlthr_data_bot")
    print(f"   Result: {success} - {msg}")
    
    # Test trading bot
    print("\n2. Testing TRADING bot...")
    success, msg = service.send_alert(BotType.TRADING, "🧪 Test message from @vlthr_trading_bot")
    print(f"   Result: {success} - {msg}")
    
    # Test devops bot
    print("\n3. Testing DEVOPS bot...")
    success, msg = service.send_alert(BotType.DEVOPS, "🧪 Test message from @vlthr_devops_bot")
    print(f"   Result: {success} - {msg}")
    
    print("\n=== Bot Token Test Complete ===")

def test_commands():
    """Test command processing"""
    service = TelegramService()
    
    print("\n=== Testing Commands ===\n")
    
    # Test data bot commands
    print("1. Testing DATA bot commands...")
    print(f"   /freshness: {service.cmd_data_freshness([])[:100]}...")
    print(f"   /ingestion: {service.cmd_data_ingestion([])[:100]}...")
    
    # Test trading bot commands
    print("\n2. Testing TRADING bot commands...")
    print(f"   /trades: {service.cmd_trading_trades([])[:100]}...")
    print(f"   /account: {service.cmd_trading_account([])[:100]}...")
    
    # Test devops bot commands
    print("\n3. Testing DEVOPS bot commands...")
    print(f"   /status: {service.cmd_devops_status([])[:100]}...")
    print(f"   /containers: {service.cmd_devops_containers([])[:100]}...")
    
    print("\n=== Command Test Complete ===")

def test_periodic_reports():
    """Test periodic report generation"""
    service = TelegramService()
    
    print("\n=== Testing Periodic Reports ===\n")
    
    print("1. Generating pipeline report...")
    pipeline_report = service.generate_pipeline_report()
    print(f"   Report length: {len(pipeline_report)} chars")
    print(f"   Preview: {pipeline_report[:200]}...")
    
    print("\n2. Generating ingestion report...")
    ingestion_report = service.generate_ingestion_report()
    print(f"   Report length: {len(ingestion_report)} chars")
    print(f"   Preview: {ingestion_report[:200]}...")
    
    print("\n=== Periodic Report Test Complete ===")

if __name__ == "__main__":
    print("Telegram Service Test Suite")
    print("=" * 50)
    
    # Run tests
    test_bot_tokens()
    test_commands()
    test_periodic_reports()
    
    print("\n" + "=" * 50)
    print("All tests completed!")
